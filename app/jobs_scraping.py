"""
Jobs de scraping - Collecte sans scoring temps réel.

Nouveau flow (batch):
1. Collecter les deals et les stocker EN ATTENTE (pas affichés)
2. Job batch Vinted toutes les 15 min pour alimenter le cache
3. Job batch scoring pour scorer les deals en attente
4. Les deals avec score >= 60 deviennent visibles
"""
import time
from datetime import datetime
from typing import List, Dict, Optional

from rq import Queue
import redis
import os

from app.core.logging import get_logger, set_trace_id
from app.utils.http_stealth import random_delay
from app.core.source_policy import SOURCE_POLICIES, get_policy
from app.services.scraping_service import (
    discover_products,
    add_scraping_log,
    ScrapingResult,
    get_enabled_sources,
)
from app.services.deal_service import persist_deal
from app.collectors.sources.courir import fetch_courir_product
from app.collectors.sources.footlocker import fetch_footlocker_product
from app.collectors.sources.size import fetch_size_product
from app.collectors.sources.jdsports import fetch_jdsports_product

logger = get_logger(__name__)

# Mapping source -> fonction de collecte
COLLECTORS = {
    "courir": fetch_courir_product,
    "footlocker": fetch_footlocker_product,
    "size": fetch_size_product,
    "jdsports": fetch_jdsports_product,
}

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def scrape_source(source: str, max_products: int = 50) -> Dict:
    """
    Job: scrape une source et stocke les deals EN ATTENTE.
    
    Les deals seront scorés par le batch job suivant.
    Ils ne seront visibles qu'une fois scorés avec score >= 60.
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting source scraping (batch mode)", source=source, max_products=max_products)
    
    if source not in COLLECTORS:
        return {
            "source": source,
            "status": "error",
            "error": f"No collector for source: {source}",
        }
    
    # Phase 1: Découverte des produits
    result, product_urls = discover_products(source)
    
    if result.status in ("error", "skipped"):
        add_scraping_log(result)
        return result.to_dict()
    
    # Phase 2: Collecte (sans scoring - sera fait en batch)
    collector = COLLECTORS[source]
    urls_to_process = list(product_urls)[:max_products]
    
    collected = 0
    new_deals = 0
    updated_deals = 0
    errors = []
    
    for url in urls_to_process:
        try:
            # Collecter le produit
            item = collector(url)
            
            # Persister en base (sans score pour l'instant)
            persist_result = persist_deal(item)
            
            collected += 1
            if persist_result.get("action") == "created":
                new_deals += 1
            else:
                updated_deals += 1
            
            logger.debug(f"Product collected", source=source, url=url)
            
            # Pause entre les produits
            random_delay(source)
            
        except Exception as e:
            errors.append(f"{url}: {str(e)[:100]}")
            logger.warning(f"Failed to collect product", source=source, url=url, error=str(e))
            continue
    
    # Mettre à jour le résultat
    result.products_found = len(product_urls)
    result.products_new = new_deals
    result.products_updated = updated_deals
    result.errors.extend(errors[:10])
    result.completed_at = datetime.utcnow()
    result.duration_seconds = (time.perf_counter() - start_time)
    
    if collected > 0:
        result.status = "success" if not errors else "partial"
    else:
        result.status = "error"
    
    add_scraping_log(result)
    
    logger.info(
        f"Source scraping completed (pending scoring)",
        source=source,
        products_found=result.products_found,
        collected=collected,
        new=new_deals,
        updated=updated_deals,
        errors=len(errors),
        duration_sec=round(result.duration_seconds, 2),
    )
    
    return {
        **result.to_dict(),
        "collected": collected,
        "pending_scoring": new_deals,  # Ces deals attendent le batch scoring
    }


def scrape_all_sources(
    sources: Optional[List[str]] = None,
    max_products_per_source: int = 30,
) -> Dict:
    """Job: scrape toutes les sources (mode batch)."""
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    if sources:
        sources_to_scrape = [s for s in sources if s in SOURCE_POLICIES]
    else:
        sources_to_scrape = get_enabled_sources()
    
    logger.info(f"Starting multi-source scraping (batch mode)", sources=sources_to_scrape)
    
    results = []
    total_found = 0
    total_new = 0
    total_updated = 0
    
    for source in sources_to_scrape:
        try:
            result = scrape_source(source, max_products=max_products_per_source)
            results.append(result)
            total_found += result.get("deals_found", 0)
            total_new += result.get("deals_new", 0)
            total_updated += result.get("deals_updated", 0)
        except Exception as e:
            logger.error(f"Failed to scrape source", source=source, error=str(e))
            results.append({
                "source": source,
                "status": "error",
                "error": str(e),
            })
        
        random_delay(source, multiplier=1.5)
    
    duration = time.perf_counter() - start_time
    
    return {
        "status": "completed",
        "sources_scraped": len(results),
        "total_found": total_found,
        "total_new": total_new,
        "total_updated": total_updated,
        "pending_vinted_batch": total_new,  # Ces deals attendent le batch Vinted
        "duration_seconds": round(duration, 2),
        "results": results,
    }


def run_vinted_batch():
    """
    Job batch: scrape Vinted pour les deals sans stats.
    Exécuté toutes les 15 minutes.
    """
    from app.services.vinted_cache_service import batch_scrape_pending_deals
    
    logger.info("Starting Vinted batch scraping")
    result = batch_scrape_pending_deals(limit=50)
    logger.info(f"Vinted batch completed: {result}")
    return result


def run_scoring_batch():
    """
    Job batch: score les deals qui ont des stats Vinted.
    Supprime les deals avec score < 60.
    """
    from app.services.vinted_cache_service import batch_rescore_deals
    
    logger.info("Starting scoring batch")
    result = batch_rescore_deals(limit=50)
    logger.info(f"Scoring batch completed: {result}")
    return result


def scheduled_scraping():
    """
    Job planifié complet (toutes les 15 min):
    1. Scraper les sources
    2. Batch Vinted
    3. Batch scoring
    """
    logger.info("=== Scheduled scraping cycle START ===")
    
    # 1. Scraper les sources
    scrape_result = scrape_all_sources(max_products_per_source=30)
    logger.info(f"Scraping done: {scrape_result.get('total_new', 0)} new deals")
    
    # 2. Batch Vinted (scrape les stats pour les deals en attente)
    vinted_result = run_vinted_batch()
    logger.info(f"Vinted batch done: {vinted_result.get('stats_saved', 0)} stats")
    
    # 3. Batch scoring (score et filtre les deals)
    scoring_result = run_scoring_batch()
    logger.info(f"Scoring done: {scoring_result.get('deals_scored', 0)} scored, {scoring_result.get('deals_deleted', 0)} deleted")
    
    logger.info("=== Scheduled scraping cycle END ===")
    
    return {
        "scraping": scrape_result,
        "vinted_batch": vinted_result,
        "scoring_batch": scoring_result,
    }
