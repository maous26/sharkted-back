"""
Jobs de scoring - Calcul automatique du FlipScore pour les deals.

Ce module:
1. Score les nouveaux deals automatiquement
2. Met à jour les scores existants périodiquement
3. Fournit des jobs pour scorer en batch
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from sqlalchemy import text
from rq import Queue
import redis
import os

from app.core.logging import get_logger, set_trace_id
from app.db.session import SessionLocal
from app.models.deal import Deal
from app.models.vinted_stats import VintedStats
from app.models.deal_score import DealScore
from app.services.vinted_service import get_vinted_stats_for_deal
from app.services.scoring_service import score_deal

logger = get_logger(__name__)

# Redis
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


async def _score_single_deal(deal_id: int, session) -> Dict:
    """
    Score un deal unique.
    
    Returns:
        Dict avec le résultat du scoring
    """
    deal = session.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        return {"deal_id": deal_id, "status": "not_found"}
    
    try:
        # Récupérer les stats Vinted
        vinted_data = await get_vinted_stats_for_deal(
            product_name=deal.title,
            brand=deal.brand or deal.seller_name,
            sale_price=deal.price
        )
        
        # Sauvegarder/Mettre à jour les stats Vinted
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
        
        # Calculer le score
        deal_data = {
            'product_name': deal.title,
            'brand': deal.brand or deal.seller_name,
            'model': deal.model,
            'category': deal.category or 'default',
            'color': deal.color,
            'gender': deal.gender,
            'discount_percent': deal.discount_percent or 0,
            'sizes_available': deal.sizes_available,
        }
        
        score_result = await score_deal(deal_data, vinted_data)
        
        # Sauvegarder/Mettre à jour le score
        existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
        if existing_score:
            for key, value in score_result.items():
                if hasattr(existing_score, key):
                    setattr(existing_score, key, value)
            existing_score.updated_at = datetime.utcnow()
        else:
            deal_score = DealScore(
                deal_id=deal_id,
                flip_score=score_result['flip_score'],
                popularity_score=score_result['popularity_score'],
                liquidity_score=score_result['liquidity_score'],
                margin_score=score_result['margin_score'],
                recommended_action=score_result['recommended_action'],
                recommended_price=score_result['recommended_price'],
                confidence=score_result['confidence'],
                explanation=score_result.get('explanation', ''),
                explanation_short=score_result.get('explanation_short', ''),
                risks=score_result.get('risks', []),
                estimated_sell_days=score_result.get('estimated_sell_days'),
                model_version=score_result.get('model_version', 'rules_v1'),
            )
            session.add(deal_score)
        
        session.commit()
        
        logger.info(
            f"Deal scored",
            deal_id=deal_id,
            title=deal.title[:50],
            flip_score=score_result['flip_score'],
            action=score_result['recommended_action'],
            margin_pct=vinted_data.get('margin_pct', 0),
        )
        
        return {
            "deal_id": deal_id,
            "status": "scored",
            "flip_score": score_result['flip_score'],
            "action": score_result['recommended_action'],
            "margin_pct": vinted_data.get('margin_pct', 0),
        }
        
    except Exception as e:
        logger.error(f"Failed to score deal {deal_id}: {e}")
        return {
            "deal_id": deal_id,
            "status": "error",
            "error": str(e)[:200],
        }


def score_new_deals(limit: int = 20) -> Dict:
    """
    Score les deals qui n'ont pas encore de score.
    
    Args:
        limit: Nombre max de deals à scorer
    
    Returns:
        Dict avec les résultats
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting scoring of new deals", limit=limit)
    
    session = SessionLocal()
    try:
        # Trouver les deals sans score
        unscored_deals = session.execute(
            text("""
                SELECT d.id 
                FROM deals d 
                LEFT JOIN deal_scores ds ON d.id = ds.deal_id 
                WHERE ds.id IS NULL 
                ORDER BY d.first_seen_at DESC 
                LIMIT :limit
            """),
            {"limit": limit}
        ).fetchall()
        
        deal_ids = [row[0] for row in unscored_deals]
        
        if not deal_ids:
            logger.info("No new deals to score")
            return {
                "status": "completed",
                "deals_scored": 0,
                "message": "No new deals to score",
            }
        
        logger.info(f"Found {len(deal_ids)} deals to score")
        
        # Scorer chaque deal
        results = []
        scored = 0
        errors = 0
        
        for deal_id in deal_ids:
            result = asyncio.run(_score_single_deal(deal_id, session))
            results.append(result)
            
            if result["status"] == "scored":
                scored += 1
            else:
                errors += 1
            
            # Pause pour rate limiting Vinted
            time.sleep(2)
        
        duration = time.perf_counter() - start_time
        
        return {
            "status": "completed",
            "deals_found": len(deal_ids),
            "deals_scored": scored,
            "errors": errors,
            "duration_seconds": round(duration, 2),
            "results": results,
        }
        
    except Exception as e:
        logger.error(f"Error in score_new_deals: {e}")
        return {
            "status": "error",
            "error": str(e),
        }
    finally:
        session.close()


def score_deal_by_id(deal_id: int) -> Dict:
    """
    Score un deal spécifique par son ID.
    
    Args:
        deal_id: ID du deal à scorer
    
    Returns:
        Dict avec le résultat
    """
    trace_id = set_trace_id()
    logger.info(f"Scoring single deal", deal_id=deal_id)
    
    session = SessionLocal()
    try:
        result = asyncio.run(_score_single_deal(deal_id, session))
        return result
    except Exception as e:
        logger.error(f"Error scoring deal {deal_id}: {e}")
        return {
            "deal_id": deal_id,
            "status": "error",
            "error": str(e),
        }
    finally:
        session.close()


def update_old_scores(older_than_hours: int = 24, limit: int = 20) -> Dict:
    """
    Met à jour les scores qui sont trop vieux.
    
    Args:
        older_than_hours: Âge minimum des scores à mettre à jour
        limit: Nombre max de deals à re-scorer
    
    Returns:
        Dict avec les résultats
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Updating old scores", older_than_hours=older_than_hours, limit=limit)
    
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
        
        old_scores = session.execute(
            text("""
                SELECT d.id 
                FROM deals d 
                JOIN deal_scores ds ON d.id = ds.deal_id 
                WHERE ds.updated_at < :cutoff 
                ORDER BY ds.updated_at ASC 
                LIMIT :limit
            """),
            {"cutoff": cutoff, "limit": limit}
        ).fetchall()
        
        deal_ids = [row[0] for row in old_scores]
        
        if not deal_ids:
            return {
                "status": "completed",
                "deals_updated": 0,
                "message": "No old scores to update",
            }
        
        results = []
        updated = 0
        
        for deal_id in deal_ids:
            result = asyncio.run(_score_single_deal(deal_id, session))
            results.append(result)
            
            if result["status"] == "scored":
                updated += 1
            
            time.sleep(2)
        
        duration = time.perf_counter() - start_time
        
        return {
            "status": "completed",
            "deals_found": len(deal_ids),
            "deals_updated": updated,
            "duration_seconds": round(duration, 2),
        }
        
    except Exception as e:
        logger.error(f"Error in update_old_scores: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        session.close()


def scheduled_scoring():
    """
    Job planifié: score les nouveaux deals et met à jour les anciens.
    """
    logger.info("Scheduled scoring started")
    
    # D'abord scorer les nouveaux
    new_result = score_new_deals(limit=10)
    
    # Puis mettre à jour les anciens
    update_result = update_old_scores(older_than_hours=12, limit=5)
    
    return {
        "new_deals": new_result,
        "updated_scores": update_result,
    }


def score_deals_after_scraping(deal_ids: List[int]) -> Dict:
    """
    Score une liste de deals après scraping.
    Appelé par le job de scraping.
    
    Args:
        deal_ids: Liste des IDs de deals à scorer
    
    Returns:
        Dict avec les résultats
    """
    if not deal_ids:
        return {"status": "skipped", "reason": "No deals to score"}
    
    trace_id = set_trace_id()
    logger.info(f"Scoring {len(deal_ids)} deals after scraping")
    
    session = SessionLocal()
    results = []
    scored = 0
    
    try:
        for deal_id in deal_ids[:10]:  # Limiter à 10 pour ne pas bloquer
            result = asyncio.run(_score_single_deal(deal_id, session))
            results.append(result)
            
            if result["status"] == "scored":
                scored += 1
            
            time.sleep(2)  # Rate limit Vinted
        
        return {
            "status": "completed",
            "deals_scored": scored,
            "total": len(deal_ids),
            "results": results,
        }
    except Exception as e:
        logger.error(f"Error in score_deals_after_scraping: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        session.close()
