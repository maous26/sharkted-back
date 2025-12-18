"""
Jobs de scraping - Collecte avec scoring AUTONOME (sans Vinted).

Flow:
1. Collecter les deals
2. Scorer immédiatement avec le scoring autonome (marque, modèle, discount)
3. Persister uniquement si score >= 60
4. Les deals sont visibles immédiatement avec leur score
"""
import time
from datetime import datetime
from typing import List, Dict, Optional

from rq import Queue
import redis
import os

from app.core.logging import get_logger, set_trace_id
from app.scheduler import is_quiet_hours
from app.utils.http_stealth import random_delay
from app.core.source_policy import SOURCE_POLICIES, get_policy
from app.services.scraping_service import (
    discover_products,
    add_scraping_log,
    ScrapingResult,
    get_enabled_sources,
)
from app.services.deal_service import get_db_session
from app.services.autonomous_scoring_service import score_deal_autonomous
from app.collectors.sources.courir import fetch_courir_product
from app.collectors.sources.footlocker import fetch_footlocker_product
from app.collectors.sources.size import fetch_size_product
from app.collectors.sources.jdsports import fetch_jdsports_product
from app.collectors.sources.asos import fetch_asos_product
from app.collectors.sources.laredoute import fetch_laredoute_product
from app.collectors.sources.bstn import fetch_bstn_product
from app.collectors.sources.footpatrol import fetch_footpatrol_product
from app.collectors.sources.printemps import fetch_printemps_product

# Models
from app.models.deal import Deal
from app.models.deal_score import DealScore
from app.repositories.deal_repository import DealRepository

logger = get_logger(__name__)

# Mapping source -> fonction de collecte
COLLECTORS = {
    "courir": fetch_courir_product,
    "footlocker": fetch_footlocker_product,
    "size": fetch_size_product,
    "jdsports": fetch_jdsports_product,
    "asos": fetch_asos_product,
    "laredoute": fetch_laredoute_product,
    "bstn": fetch_bstn_product,
    "footpatrol": fetch_footpatrol_product,
    "printemps": fetch_printemps_product,
}

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MIN_SCORE = 60
MIN_DISCOUNT = 30  # Exclure produits sans remise significative


def persist_deal_with_autonomous_score(item, score_data: Dict, session) -> Dict:
    """Persiste un deal avec son score autonome."""
    repo = DealRepository(session)
    existing = repo.get_by_source_and_id(item.source, item.external_id)
    was_existing = existing is not None
    
    deal = repo.upsert(item)
    deal_id = deal.id
    
    # Sauvegarder le score
    existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
    if existing_score:
        # Update
        for key, value in score_data.items():
            if hasattr(existing_score, key) and key not in ['score_breakdown']:
                setattr(existing_score, key, value)
        existing_score.score_breakdown = score_data.get('score_breakdown', {})
        existing_score.updated_at = datetime.utcnow()
    else:
        # Create
        deal_score = DealScore(
            deal_id=deal_id,
            flip_score=score_data.get('flip_score', 0),
            margin_score=score_data.get('discount_score'),  # Use discount as proxy
            liquidity_score=score_data.get('brand_score'),  # Use brand as proxy
            popularity_score=score_data.get('model_score'),
            recommended_action=score_data.get('recommended_action'),
            recommended_price=None,  # No Vinted data
            confidence=score_data.get('confidence'),
            explanation=score_data.get('explanation'),
            explanation_short=score_data.get('explanation_short'),
            risks=score_data.get('risks', []),
            score_breakdown=score_data.get('score_breakdown', {}),
            model_version=score_data.get('model_version', 'autonomous_v1'),
        )
        session.add(deal_score)
    
    return {
        "id": deal_id,
        "action": "updated" if was_existing else "created",
        "flip_score": score_data.get('flip_score', 0),
    }


def scrape_source(source: str, max_products: int = 50, min_score: int = MIN_SCORE) -> Dict:
    """
    Scrape une source avec scoring AUTONOME immédiat.
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting scraping with autonomous scoring", source=source, max_products=max_products)
    
    if source not in COLLECTORS:
        return {"source": source, "status": "error", "error": f"No collector for: {source}"}
    
    result, product_urls = discover_products(source)
    
    if result.status in ("error", "skipped"):
        add_scraping_log(result)
        return result.to_dict()
    
    collector = COLLECTORS[source]
    urls_to_process = list(product_urls)[:max_products]
    
    collected = 0
    new_deals = 0
    updated_deals = 0
    skipped_low_score = 0
    errors = []
    
    from app.db.session import SessionLocal
    session = SessionLocal()
    
    try:
        for url in urls_to_process:
            try:
                # 1. Collecter
                item = collector(url)
                collected += 1
                
                # 2. Filtrer les produits sans remise
                if not item.discount_percent or item.discount_percent < MIN_DISCOUNT:
                    skipped_low_score += 1
                    logger.debug(f"Skipped (no discount)", title=item.title[:30], discount=item.discount_percent)
                    continue
                
                # 3. Scorer avec le scoring autonome
                deal_data = {
                    "title": item.title,
                    "brand": item.brand or item.seller_name,
                    "model": item.model,
                    "category": item.category,
                    "discount_percent": item.discount_percent,
                    "price": item.price,
                    "sizes_available": item.sizes_available,
                }
                score_result = score_deal_autonomous(deal_data)
                flip_score = score_result.get('flip_score', 0)
                
                # 4. Filtrer
                if flip_score < min_score:
                    skipped_low_score += 1
                    logger.debug(f"Skipped (score {flip_score:.1f})", title=item.title[:30])
                    continue
                
                # 5. Persister avec score
                persist_result = persist_deal_with_autonomous_score(item, score_result, session)
                session.commit()
                
                if persist_result.get("action") == "created":
                    new_deals += 1
                    logger.info(f"NEW: {item.title[:40]} | Score: {flip_score:.1f}", source=source)
                else:
                    updated_deals += 1
                
                random_delay(source)
                
            except Exception as e:
                session.rollback()
                errors.append(f"{url}: {str(e)[:80]}")
                logger.warning(f"Error: {e}", url=url[:50])
                continue
    finally:
        session.close()
    
    result.products_found = len(product_urls)
    result.products_new = new_deals
    result.products_updated = updated_deals
    result.errors.extend(errors[:10])
    result.completed_at = datetime.utcnow()
    result.duration_seconds = time.perf_counter() - start_time
    result.status = "success" if (new_deals + updated_deals) > 0 else ("partial" if errors else "completed")
    
    add_scraping_log(result)
    
    logger.info(
        f"Scraping done",
        source=source,
        found=result.products_found,
        new=new_deals,
        updated=updated_deals,
        skipped=skipped_low_score,
        duration=round(result.duration_seconds, 1),
    )
    
    return {
        **result.to_dict(),
        "collected": collected,
        "skipped_low_score": skipped_low_score,
        "min_score": min_score,
    }


def scrape_all_sources(
    sources: Optional[List[str]] = None,
    max_products_per_source: int = 30,
    min_score: int = MIN_SCORE,
) -> Dict:
    """Scrape toutes les sources avec scoring autonome."""
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    if sources:
        sources_to_scrape = [s for s in sources if s in SOURCE_POLICIES]
    else:
        sources_to_scrape = get_enabled_sources()
    
    logger.info(f"Multi-source scraping", sources=sources_to_scrape)
    
    results = []
    total_new = 0
    total_skipped = 0
    
    for source in sources_to_scrape:
        try:
            result = scrape_source(source, max_products=max_products_per_source, min_score=min_score)
            results.append(result)
            total_new += result.get("deals_new", 0)
            total_skipped += result.get("skipped_low_score", 0)
        except Exception as e:
            logger.error(f"Source failed: {source}", error=str(e))
            results.append({"source": source, "status": "error", "error": str(e)})
        
        random_delay(source, multiplier=1.5)
    
    return {
        "status": "completed",
        "sources_scraped": len(results),
        "total_new": total_new,
        "total_skipped": total_skipped,
        "duration_seconds": round(time.perf_counter() - start_time, 2),
        "results": results,
    }


def scheduled_scraping():
    """Job planifié - Skip pendant les heures de pause (minuit-7h Paris)."""
    # Vérifier si on est dans les heures de pause
    if is_quiet_hours():
        logger.info("=== Scheduled scraping SKIPPED (quiet hours: 00h-07h Paris) ===")
        return {"status": "skipped", "reason": "quiet_hours", "total_new": 0}
    
    logger.info("=== Scheduled scraping START ===")
    result = scrape_all_sources(max_products_per_source=30)
    logger.info(f"=== Scheduled scraping END: {result.get('total_new', 0)} new deals ===")
    return result
