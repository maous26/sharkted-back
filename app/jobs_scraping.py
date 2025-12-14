"""
Jobs de scraping - Découverte et collecte automatique des deals.

Ce module contient les jobs RQ pour:
1. Découvrir les produits sur les pages de listing
2. Collecter les détails de chaque produit
3. Orchestrer le scraping complet d'une source
4. Scorer automatiquement les nouveaux deals
"""
import time
from datetime import datetime
from typing import List, Dict, Optional

from rq import Queue, get_current_job
import redis
import os

from app.core.logging import get_logger, set_trace_id
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

# Redis connection for enqueueing
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def scrape_source(source: str, max_products: int = 50, auto_score: bool = True) -> Dict:
    """
    Job principal: scrape une source complète.
    
    1. Découvre les produits via les pages de listing
    2. Collecte les détails de chaque produit (limité à max_products)
    3. Persiste en base
    4. Score automatiquement les nouveaux deals
    5. Log les résultats
    
    Args:
        source: Nom de la source (courir, footlocker, etc.)
        max_products: Nombre max de produits à collecter
        auto_score: Si True, score automatiquement les nouveaux deals
    
    Returns:
        Dict avec les stats du scraping
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting source scraping", source=source, max_products=max_products)
    
    # Vérifier si la source est supportée
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
    
    # Phase 2: Collecte des détails (limité)
    collector = COLLECTORS[source]
    urls_to_process = list(product_urls)[:max_products]
    
    collected = 0
    new_deals = 0
    updated_deals = 0
    errors = []
    new_deal_ids = []  # Track IDs of new deals for scoring
    
    for url in urls_to_process:
        try:
            # Collecter le produit
            item = collector(url)
            
            # Persister en base
            persist_result = persist_deal(item)
            
            collected += 1
            if persist_result.get("action") == "created":
                new_deals += 1
                new_deal_ids.append(persist_result["id"])
            else:
                updated_deals += 1
                
            logger.debug(f"Product collected", source=source, url=url)
            
            # Pause entre les produits
            time.sleep(1.5)
            
        except Exception as e:
            errors.append(f"{url}: {str(e)[:100]}")
            logger.warning(f"Failed to collect product", source=source, url=url, error=str(e))
            continue
    
    # Phase 3: Scoring automatique des nouveaux deals
    scoring_result = None
    if auto_score and new_deal_ids:
        try:
            from app.jobs_scoring import score_deals_after_scraping
            logger.info(f"Auto-scoring {len(new_deal_ids)} new deals", source=source)
            scoring_result = score_deals_after_scraping(new_deal_ids)
            logger.info(
                f"Scoring completed",
                source=source,
                deals_scored=scoring_result.get("deals_scored", 0),
            )
        except Exception as e:
            logger.error(f"Failed to auto-score deals", source=source, error=str(e))
            scoring_result = {"status": "error", "error": str(e)}
    
    # Mettre à jour le résultat
    result.products_found = len(product_urls)
    result.products_new = new_deals
    result.products_updated = updated_deals
    result.errors.extend(errors[:10])  # Limiter les erreurs loggées
    result.completed_at = datetime.utcnow()
    result.duration_seconds = (time.perf_counter() - start_time)
    
    if collected > 0:
        result.status = "success" if not errors else "partial"
    else:
        result.status = "error"
    
    # Sauvegarder le log
    add_scraping_log(result)
    
    logger.info(
        f"Source scraping completed",
        source=source,
        products_found=result.products_found,
        collected=collected,
        new=new_deals,
        updated=updated_deals,
        errors=len(errors),
        duration_sec=round(result.duration_seconds, 2),
    )
    
    response = result.to_dict()
    if scoring_result:
        response["scoring"] = scoring_result
    
    return response


def scrape_all_sources(sources: Optional[List[str]] = None, max_products_per_source: int = 30, auto_score: bool = True) -> Dict:
    """
    Job: scrape toutes les sources activées.
    
    Args:
        sources: Liste des sources à scraper (None = toutes les activées)
        max_products_per_source: Limite par source
        auto_score: Si True, score automatiquement les nouveaux deals
    
    Returns:
        Dict avec les résultats agrégés
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    # Déterminer les sources à scraper
    if sources:
        sources_to_scrape = [s for s in sources if s in SOURCE_POLICIES]
    else:
        sources_to_scrape = get_enabled_sources()
    
    logger.info(f"Starting multi-source scraping", sources=sources_to_scrape)
    
    results = []
    total_found = 0
    total_new = 0
    total_updated = 0
    total_scored = 0
    
    for source in sources_to_scrape:
        try:
            result = scrape_source(source, max_products=max_products_per_source, auto_score=auto_score)
            results.append(result)
            total_found += result.get("deals_found", 0)
            total_new += result.get("deals_new", 0)
            total_updated += result.get("deals_updated", 0)
            if result.get("scoring"):
                total_scored += result["scoring"].get("deals_scored", 0)
        except Exception as e:
            logger.error(f"Failed to scrape source", source=source, error=str(e))
            results.append({
                "source": source,
                "status": "error",
                "error": str(e),
            })
        
        # Pause entre les sources
        time.sleep(5)
    
    duration = time.perf_counter() - start_time
    
    return {
        "status": "completed",
        "sources_scraped": len(results),
        "total_found": total_found,
        "total_new": total_new,
        "total_updated": total_updated,
        "total_scored": total_scored,
        "duration_seconds": round(duration, 2),
        "results": results,
    }


def scheduled_scraping():
    """
    Job planifié: exécuté par le scheduler à intervalles réguliers.
    Scrape toutes les sources activées et score les nouveaux deals.
    """
    logger.info("Scheduled scraping started")
    return scrape_all_sources(max_products_per_source=20, auto_score=True)
