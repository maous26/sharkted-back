"""
Job de scoring Vinted en batch - Score tous les deals qualifiés.
"""
import time
from datetime import datetime
from typing import Dict

from app.core.logging import get_logger, set_trace_id
from app.db.session import SessionLocal
from app.models.deal import Deal
from app.models.deal_score import DealScore
from app.models.vinted_stats import VintedStats
from app.services.vinted_service import VintedService

logger = get_logger(__name__)
vinted_service = VintedService()


def score_deals_vinted_batch(min_score: float = 60, limit: int = 100) -> Dict:
    """
    Score en batch les deals qualifiés qui nont pas encore de stats Vinted.
    
    Args:
        min_score: Score minimum pour être éligible
        limit: Nombre max de deals à scorer
    
    Returns:
        Dict avec les résultats
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting Vinted batch scoring", min_score=min_score, limit=limit)
    
    session = SessionLocal()
    try:
        # Trouver les deals qualifiés sans stats Vinted
        deals = session.query(Deal).join(
            DealScore, Deal.id == DealScore.deal_id
        ).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        ).filter(
            Deal.in_stock == True,
            DealScore.flip_score >= min_score,
            VintedStats.id == None  # Pas encore de stats Vinted
        ).order_by(
            DealScore.flip_score.desc()  # Prioriser les meilleurs scores
        ).limit(limit).all()
        
        logger.info(f"Found {len(deals)} deals to score with Vinted")
        
        results = {
            "total": len(deals),
            "scored": 0,
            "positive_margin": 0,
            "errors": 0,
            "details": []
        }
        
        for i, deal in enumerate(deals):
            try:
                logger.info(f"[{i+1}/{len(deals)}] Scoring: {deal.title[:50]}")
                
                # Récupérer les stats Vinted
                stats = vinted_service.get_market_stats(
                    product_name=deal.title,
                    brand=deal.brand or deal.seller_name,
                    current_price=deal.price
                )
                
                if stats.get("error"):
                    logger.warning(f"Vinted error for deal {deal.id}: {stats.get(error)}")
                    results["errors"] += 1
                    results["details"].append({
                        "deal_id": deal.id,
                        "status": "error",
                        "error": stats.get("error")
                    })
                    continue
                
                # Sauvegarder les stats
                vinted_stats = VintedStats(
                    deal_id=deal.id,
                    nb_listings=stats.get("nb_listings", 0),
                    price_min=stats.get("price_min"),
                    price_max=stats.get("price_max"),
                    price_avg=stats.get("price_avg"),
                    price_median=stats.get("price_median"),
                    margin_euro=stats.get("margin_euro"),
                    margin_pct=stats.get("margin_pct"),
                    liquidity_score=stats.get("liquidity_score"),
                    search_query=deal.title[:100]
                )
                session.add(vinted_stats)
                session.commit()
                
                results["scored"] += 1
                if stats.get("margin_pct", 0) > 0:
                    results["positive_margin"] += 1
                
                results["details"].append({
                    "deal_id": deal.id,
                    "title": deal.title[:40],
                    "status": "scored",
                    "margin_pct": stats.get("margin_pct", 0)
                })
                
                logger.info(f"Scored deal {deal.id}: margin={stats.get(margin_pct, 0)}%")
                
                # Rate limiting
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error scoring deal {deal.id}: {e}")
                results["errors"] += 1
                session.rollback()
        
        duration = time.perf_counter() - start_time
        logger.info(
            f"Vinted batch scoring completed",
            scored=results["scored"],
            positive_margin=results["positive_margin"],
            errors=results["errors"],
            duration_sec=round(duration, 2)
        )
        
        return results
        
    except Exception as e:
        logger.error(f"Vinted batch job failed: {e}")
        return {"error": str(e)}
    finally:
        session.close()
