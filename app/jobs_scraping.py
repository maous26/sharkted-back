"""
Jobs de scraping - Découverte et collecte avec tracking de prix.

Ce module contient les jobs RQ pour:
1. Découvrir les produits via scraping en couches
2. Collecter les détails de chaque produit
3. Tracker l'historique des prix pour détecter les drops
4. Scorer automatiquement les nouveaux deals
"""
import time
from datetime import datetime
from typing import List, Dict, Optional

from rq import Queue, get_current_job
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
from app.services.price_tracking_service import record_price_observation
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


def scrape_source(source: str, max_products: int = 50, auto_score: bool = True) -> Dict:
    """
    Job principal: scrape une source complète avec tracking de prix.
    
    1. Découvre les produits via les pages de listing
    2. Collecte les détails de chaque produit
    3. Persiste en base + enregistre l'observation de prix
    4. Détecte les price drops
    5. Score automatiquement les nouveaux deals
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting source scraping", source=source, max_products=max_products)
    
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
    
    # Phase 2: Collecte des détails
    collector = COLLECTORS[source]
    urls_to_process = list(product_urls)[:max_products]
    
    collected = 0
    new_deals = 0
    updated_deals = 0
    price_drops = 0
    errors = []
    new_deal_ids = []
    
    for url in urls_to_process:
        try:
            # Collecter le produit
            item = collector(url)
            
            # Persister en base
            persist_result = persist_deal(item)
            deal_id = persist_result["id"]
            
            collected += 1
            if persist_result.get("action") == "created":
                new_deals += 1
                new_deal_ids.append(deal_id)
            else:
                updated_deals += 1
            
            # Phase 3: Enregistrer l'observation de prix et détecter les drops
            is_drop, drop_pct = record_price_observation(
                deal_id=deal_id,
                price=item.price,
                original_price=item.original_price,
                source_url=url,
            )
            
            if is_drop:
                price_drops += 1
                logger.info(
                    f"PRICE DROP DETECTED!",
                    source=source,
                    deal_id=deal_id,
                    title=item.title[:50],
                    drop_percent=drop_pct,
                )
            
            logger.debug(f"Product collected", source=source, url=url)
            
            # Pause entre les produits
            random_delay(source)
            
        except Exception as e:
            errors.append(f"{url}: {str(e)[:100]}")
            logger.warning(f"Failed to collect product", source=source, url=url, error=str(e))
            continue
    
    # Phase 4: Scoring automatique des nouveaux deals
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
    result.errors.extend(errors[:10])
    result.completed_at = datetime.utcnow()
    result.duration_seconds = (time.perf_counter() - start_time)
    
    if collected > 0:
        result.status = "success" if not errors else "partial"
    else:
        result.status = "error"
    
    add_scraping_log(result)
    
    logger.info(
        f"Source scraping completed",
        source=source,
        products_found=result.products_found,
        collected=collected,
        new=new_deals,
        updated=updated_deals,
        price_drops=price_drops,
        errors=len(errors),
        duration_sec=round(result.duration_seconds, 2),
    )
    
    response = result.to_dict()
    response["price_drops_detected"] = price_drops
    if scoring_result:
        response["scoring"] = scoring_result
    
    return response


def scrape_all_sources(
    sources: Optional[List[str]] = None,
    max_products_per_source: int = 30,
    auto_score: bool = True,
) -> Dict:
    """Job: scrape toutes les sources activées."""
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
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
    total_drops = 0
    
    for source in sources_to_scrape:
        try:
            result = scrape_source(source, max_products=max_products_per_source, auto_score=auto_score)
            results.append(result)
            total_found += result.get("deals_found", 0)
            total_new += result.get("deals_new", 0)
            total_updated += result.get("deals_updated", 0)
            total_drops += result.get("price_drops_detected", 0)
            if result.get("scoring"):
                total_scored += result["scoring"].get("deals_scored", 0)
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
        "total_scored": total_scored,
        "total_price_drops": total_drops,
        "duration_seconds": round(duration, 2),
        "results": results,
    }


def scrape_watchlist(deal_ids: List[int], max_deals: int = 20) -> Dict:
    """
    Job: scrape les produits de la watchlist pour détecter les drops.
    
    Haute fréquence, faible volume - optimisé pour la détection rapide.
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Scraping watchlist", deal_count=len(deal_ids))
    
    from app.db.session import SessionLocal
    from app.models.deal import Deal
    
    session = SessionLocal()
    drops_detected = 0
    checked = 0
    errors = []
    
    try:
        # Récupérer les deals à vérifier
        deals = session.query(Deal).filter(Deal.id.in_(deal_ids[:max_deals])).all()
        
        for deal in deals:
            try:
                # Collecter le prix actuel
                collector = COLLECTORS.get(deal.source)
                if not collector:
                    continue
                
                item = collector(deal.url)
                checked += 1
                
                # Enregistrer et détecter drop
                is_drop, drop_pct = record_price_observation(
                    deal_id=deal.id,
                    price=item.price,
                    original_price=item.original_price,
                    source_url=deal.url,
                    session=session,
                )
                
                if is_drop:
                    drops_detected += 1
                    logger.info(
                        f"WATCHLIST DROP!",
                        deal_id=deal.id,
                        title=deal.title[:50],
                        drop_percent=drop_pct,
                    )
                
                random_delay(source, multiplier=0.5)
                
            except Exception as e:
                errors.append(f"{deal.id}: {str(e)[:50]}")
                continue
        
        session.commit()
        
    except Exception as e:
        session.rollback()
        logger.error(f"Watchlist scrape failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        session.close()
    
    duration = time.perf_counter() - start_time
    
    return {
        "status": "completed",
        "deals_checked": checked,
        "drops_detected": drops_detected,
        "errors": len(errors),
        "duration_seconds": round(duration, 2),
    }


def scheduled_scraping():
    """
    Job planifié: exécute le scraping selon la stratégie en couches.
    """
    from app.services.smart_scheduler import get_scheduler, ScrapeLayer
    
    logger.info("Scheduled scraping started")
    
    scheduler = get_scheduler()
    jobs = scheduler.get_next_jobs(max_jobs=3)
    
    results = []
    for job in jobs:
        if job["type"] == "watchlist":
            result = scrape_watchlist(job["deal_ids"])
            results.append({"type": "watchlist", "result": result})
        elif job["type"] == "scrape":
            result = scrape_source(
                job["source"],
                max_products=job["max_products"],
                auto_score=True,
            )
            results.append({"type": "scrape", "source": job["source"], "result": result})
            
            # Mettre à jour le scheduler
            scheduler.mark_completed(
                source=job["source"],
                layer=job["layer"],
                success=result.get("status") != "error",
                new_products=result.get("deals_new", 0),
            )
    
    return {
        "jobs_executed": len(results),
        "results": results,
    }
