"""
Jobs de scraping - Découverte et collecte avec scoring INSTANTANÉ.

Ce module:
1. Découvre les produits via scraping
2. Score IMMÉDIATEMENT chaque produit (avant insertion)
3. Persiste UNIQUEMENT les deals avec score >= 60
4. Évite de charger la base avec des deals de faible qualité
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
from app.services.deal_service import persist_deal, get_db_session
from app.services.price_tracking_service import record_price_observation
from app.services.instant_scoring_service import (
    score_deal_instant_sync,
    should_persist_deal,
    MIN_SCORE_THRESHOLD,
)
from app.collectors.sources.courir import fetch_courir_product
from app.collectors.sources.footlocker import fetch_footlocker_product
from app.collectors.sources.size import fetch_size_product
from app.collectors.sources.jdsports import fetch_jdsports_product

# Models for direct insertion with score
from app.models.deal import Deal
from app.models.vinted_stats import VintedStats
from app.models.deal_score import DealScore
from app.repositories.deal_repository import DealRepository

logger = get_logger(__name__)

# Mapping source -> fonction de collecte
COLLECTORS = {
    "courir": fetch_courir_product,
    "footlocker": fetch_footlocker_product,
    "size": fetch_size_product,
    "jdsports": fetch_jdsports_product,
}

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def persist_deal_with_score(item, vinted_data: Dict, score_data: Dict, session) -> Dict:
    """
    Persiste un deal AVEC son score et stats Vinted en une seule transaction.
    """
    repo = DealRepository(session)
    existing = repo.get_by_source_and_id(item.source, item.external_id)
    was_existing = existing is not None
    old_price = existing.price if existing else None
    
    # Upsert le deal
    deal = repo.upsert(item)
    deal_id = deal.id
    
    # Sauvegarder/Mettre à jour les stats Vinted
    if vinted_data:
        existing_vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        if existing_vinted:
            for key, value in vinted_data.items():
                if key != 'sample_listings' and hasattr(existing_vinted, key):
                    setattr(existing_vinted, key, value)
            existing_vinted.sample_listings = vinted_data.get('sample_listings', [])
        else:
            vinted_stats = VintedStats(
                deal_id=deal_id,
                nb_listings=vinted_data.get('nb_listings', 0),
                price_min=vinted_data.get('price_min'),
                price_max=vinted_data.get('price_max'),
                price_avg=vinted_data.get('price_avg'),
                price_median=vinted_data.get('price_median'),
                price_p25=vinted_data.get('price_p25'),
                price_p75=vinted_data.get('price_p75'),
                coefficient_variation=vinted_data.get('coefficient_variation'),
                margin_euro=vinted_data.get('margin_euro'),
                margin_pct=vinted_data.get('margin_pct'),
                liquidity_score=vinted_data.get('liquidity_score'),
                sample_listings=vinted_data.get('sample_listings', []),
                search_query=vinted_data.get('query_used', '')
            )
            session.add(vinted_stats)
    
    # Sauvegarder/Mettre à jour le score
    if score_data:
        existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
        if existing_score:
            for key, value in score_data.items():
                if hasattr(existing_score, key):
                    setattr(existing_score, key, value)
            existing_score.updated_at = datetime.utcnow()
        else:
            deal_score = DealScore(
                deal_id=deal_id,
                flip_score=score_data.get('flip_score', 0),
                margin_score=score_data.get('margin_score'),
                liquidity_score=score_data.get('liquidity_score'),
                popularity_score=score_data.get('popularity_score'),
                recommended_action=score_data.get('recommended_action'),
                recommended_price=score_data.get('recommended_price'),
                confidence=score_data.get('confidence'),
                explanation=score_data.get('explanation'),
                explanation_short=score_data.get('explanation_short'),
                risks=score_data.get('risks', []),
                score_breakdown=score_data.get('score_breakdown', {}),
                model_version=score_data.get('model_version', 'v2_instant'),
            )
            session.add(deal_score)
    
    return {
        "id": deal_id,
        "source": deal.source,
        "external_id": deal.external_id,
        "action": "updated" if was_existing else "created",
        "price_changed": was_existing and old_price != deal.price,
        "old_price": old_price if was_existing else None,
        "new_price": deal.price,
        "flip_score": score_data.get('flip_score', 0) if score_data else 0,
    }


def scrape_source(source: str, max_products: int = 50, min_score: int = MIN_SCORE_THRESHOLD) -> Dict:
    """
    Job principal: scrape une source avec SCORING INSTANTANÉ.
    
    Flow:
    1. Découvre les produits via les pages de listing
    2. Pour chaque produit:
       a. Collecte les détails
       b. Score IMMÉDIATEMENT (Vinted + calcul)
       c. Si score >= min_score: persiste en base AVEC le score
       d. Sinon: skip (ne charge pas la base)
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting source scraping with instant scoring", 
                source=source, max_products=max_products, min_score=min_score)
    
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
    
    # Phase 2: Collecte + Scoring instantané + Persistence conditionnelle
    collector = COLLECTORS[source]
    urls_to_process = list(product_urls)[:max_products]
    
    collected = 0
    new_deals = 0
    updated_deals = 0
    skipped_low_score = 0
    scoring_errors = 0
    price_drops = 0
    errors = []
    
    from app.db.session import SessionLocal
    session = SessionLocal()
    
    try:
        for url in urls_to_process:
            try:
                # 1. Collecter le produit
                item = collector(url)
                collected += 1
                
                # 2. Scorer IMMÉDIATEMENT
                vinted_data, score_data, flip_score = score_deal_instant_sync(item)
                
                if not score_data:
                    scoring_errors += 1
                    # En cas d'erreur de scoring, on skip le deal
                    logger.warning(f"Scoring failed, skipping deal", url=url)
                    continue
                
                # 3. Vérifier si le score est suffisant
                if not should_persist_deal(flip_score, min_score):
                    skipped_low_score += 1
                    logger.debug(
                        f"Deal skipped (score {flip_score:.1f} < {min_score})",
                        source=source,
                        title=item.title[:40],
                        flip_score=flip_score
                    )
                    continue
                
                # 4. Persister le deal AVEC son score
                persist_result = persist_deal_with_score(item, vinted_data, score_data, session)
                
                if persist_result.get("action") == "created":
                    new_deals += 1
                    logger.info(
                        f"NEW DEAL saved with score {flip_score:.1f}",
                        source=source,
                        title=item.title[:40],
                        flip_score=flip_score,
                        price=item.price
                    )
                else:
                    updated_deals += 1
                    if persist_result.get("price_changed"):
                        price_drops += 1
                
                # Commit après chaque deal réussi
                session.commit()
                
                # Pause entre les produits
                random_delay(source)
                
            except Exception as e:
                session.rollback()
                errors.append(f"{url}: {str(e)[:100]}")
                logger.warning(f"Failed to process product", source=source, url=url, error=str(e))
                continue
        
    finally:
        session.close()
    
    # Mettre à jour le résultat
    result.products_found = len(product_urls)
    result.products_new = new_deals
    result.products_updated = updated_deals
    result.errors.extend(errors[:10])
    result.completed_at = datetime.utcnow()
    result.duration_seconds = (time.perf_counter() - start_time)
    
    if new_deals > 0 or updated_deals > 0:
        result.status = "success" if not errors else "partial"
    else:
        result.status = "completed" if skipped_low_score > 0 else "error"
    
    add_scraping_log(result)
    
    logger.info(
        f"Source scraping completed",
        source=source,
        products_found=result.products_found,
        collected=collected,
        new=new_deals,
        updated=updated_deals,
        skipped_low_score=skipped_low_score,
        scoring_errors=scoring_errors,
        price_drops=price_drops,
        errors=len(errors),
        duration_sec=round(result.duration_seconds, 2),
    )
    
    return {
        **result.to_dict(),
        "collected": collected,
        "skipped_low_score": skipped_low_score,
        "scoring_errors": scoring_errors,
        "price_drops_detected": price_drops,
        "min_score_threshold": min_score,
    }


def scrape_all_sources(
    sources: Optional[List[str]] = None,
    max_products_per_source: int = 30,
    min_score: int = MIN_SCORE_THRESHOLD,
) -> Dict:
    """Job: scrape toutes les sources activées avec scoring instantané."""
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    if sources:
        sources_to_scrape = [s for s in sources if s in SOURCE_POLICIES]
    else:
        sources_to_scrape = get_enabled_sources()
    
    logger.info(f"Starting multi-source scraping", sources=sources_to_scrape, min_score=min_score)
    
    results = []
    total_found = 0
    total_new = 0
    total_updated = 0
    total_skipped = 0
    total_drops = 0
    
    for source in sources_to_scrape:
        try:
            result = scrape_source(source, max_products=max_products_per_source, min_score=min_score)
            results.append(result)
            total_found += result.get("deals_found", 0)
            total_new += result.get("deals_new", 0)
            total_updated += result.get("deals_updated", 0)
            total_skipped += result.get("skipped_low_score", 0)
            total_drops += result.get("price_drops_detected", 0)
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
        "total_skipped_low_score": total_skipped,
        "total_price_drops": total_drops,
        "min_score_threshold": min_score,
        "duration_seconds": round(duration, 2),
        "results": results,
    }


def scheduled_scraping():
    """Job planifié: exécute le scraping avec scoring instantané."""
    logger.info("Scheduled scraping started (instant scoring mode)")
    
    # Scraper toutes les sources actives
    result = scrape_all_sources(max_products_per_source=30, min_score=MIN_SCORE_THRESHOLD)
    
    logger.info(
        f"Scheduled scraping completed",
        new_deals=result.get("total_new", 0),
        skipped=result.get("total_skipped_low_score", 0),
    )
    
    return result
