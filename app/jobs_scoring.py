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
        # Pre-scoring: Scoring heuristique rapide (SANS Vinted)
        deal_data = {
            'product_name': deal.title,
            'brand': deal.brand or deal.seller_name,
            'model': deal.model,
            'category': deal.category or 'default',
            'color': deal.color,
            'gender': deal.gender,
            'discount_percent': deal.discount_percent or 0,
            'sizes_available': deal.sizes_available,
            'price': deal.price,
            'original_price': deal.original_price,
            'sale_price': deal.price
        }

        # 1. Calcul du score préliminaire (Règles uniquement)
        pre_score_result = await score_deal(deal_data, vinted_stats=None)
        pre_flip_score = pre_score_result.get('flip_score', 0)
        
        logger.info(f"Pre-score for deal {deal_id}: {pre_flip_score}")

        vinted_data = None
        
        # 2. Sniper Logic: Scraper Vinted seulement si le potentiel est là (> 65 pour être large)
        # OU si c'est une marque "Hype" connue (nike, jordan...)
        is_hype_brand = deal.brand and deal.brand.lower() in ['nike', 'jordan', 'yeezy', 'adidas', 'new balance']
        
        if pre_flip_score >= 65 or (is_hype_brand and pre_flip_score >= 50):
            logger.info(f"Sniper triggered for deal {deal_id} (Score: {pre_flip_score})")
            try:
                # Récupérer les stats Vinted (via Browser Worker / Proxy Gratuit)
                vinted_data = await get_vinted_stats_for_deal(
                    product_name=deal.title,
                    brand=deal.brand or deal.seller_name,
                    sale_price=deal.price
                )
            except Exception as e:
                logger.error(f"Vinted scrape error for {deal_id}: {e}")
                vinted_data = None
        else:
            logger.info(f"Skipping Vinted scrape for deal {deal_id} (Score too low: {pre_flip_score})")

        # Sauvegarder les stats Vinted SI on en a trouvé
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

        # 3. Calcul du score FINAL (Avec ou sans Vinted)
        # Si vinted_data est présent, le score sera ajusté avec les vraies marges
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
        
        final_margin = vinted_data.get('margin_pct', 0) if vinted_data else score_result.get('estimated_margin_pct', 0)

        logger.info(
            f"Deal scored FINAL",
            deal_id=deal_id,
            title=deal.title[:50],
            flip_score=score_result['flip_score'],
            action=score_result['recommended_action'],
            margin_pct=final_margin,
            with_vinted=bool(vinted_data)
        )
        
        return {
            "deal_id": deal_id,
            "status": "scored",
            "flip_score": score_result['flip_score'],
            "action": score_result['recommended_action'],
            "margin_pct": final_margin,
            "vinted_checked": bool(vinted_data)
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




def rescore_deals_batch(limit: int = 50, force: bool = False) -> Dict:
    """Rescore des deals en batch avec les stats Vinted."""
    import asyncio
    from app.db.session import SessionLocal
    from app.models.deal import Deal
    from app.models.vinted_stats import VintedStats
    from app.models.deal_score import DealScore
    from app.services.vinted_service import get_vinted_stats_for_deal
    from app.services.scoring_service import score_deal
    from datetime import datetime
    
    db = SessionLocal()
    results = {"processed": 0, "scored": 0, "errors": 0, "no_data": 0}
    
    try:
        query = db.query(Deal)
        if not force:
            query = query.outerjoin(VintedStats).filter(VintedStats.id == None)
        
        deals = query.order_by(Deal.id.desc()).limit(limit).all()
        logger.info(f"Rescraping Vinted stats for {len(deals)} deals")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        for deal in deals:
            results["processed"] += 1
            try:
                logger.info(f"Processing deal {deal.id}: {deal.title[:50]}...")
                stats = loop.run_until_complete(get_vinted_stats_for_deal(deal.title, deal.brand, deal.price))
                
                if not stats or stats.get("nb_listings", 0) == 0:
                    results["no_data"] += 1
                    continue
                
                vinted_stat = db.query(VintedStats).filter(VintedStats.deal_id == deal.id).first()
                if not vinted_stat:
                    vinted_stat = VintedStats(deal_id=deal.id)
                    db.add(vinted_stat)
                
                vinted_stat.nb_listings = stats.get("nb_listings", 0)
                vinted_stat.price_min = stats.get("price_min")
                vinted_stat.price_max = stats.get("price_max")
                vinted_stat.price_avg = stats.get("price_avg")
                vinted_stat.price_median = stats.get("price_median")
                vinted_stat.margin_euro = stats.get("margin_euro")
                vinted_stat.margin_pct = stats.get("margin_pct")
                vinted_stat.liquidity_score = stats.get("liquidity_score")
                vinted_stat.source_type = stats.get("source_type")
                vinted_stat.coefficient = stats.get("coefficient")
                vinted_stat.fetched_at = datetime.utcnow()
                
                deal_data = {
                    "brand": deal.brand,
                    "category": deal.category or "default",
                    "discount_percent": deal.discount_percent or 0,
                    "sizes_available": deal.sizes_available,
                    "color": deal.color
                }
                
                score_result = loop.run_until_complete(score_deal(deal_data, stats))
                
                deal_score = db.query(DealScore).filter(DealScore.deal_id == deal.id).first()
                if not deal_score:
                    deal_score = DealScore(deal_id=deal.id)
                    db.add(deal_score)
                
                deal_score.flip_score = score_result.get("flip_score", 0)
                deal_score.recommended_action = score_result.get("recommended_action")
                deal_score.recommended_price = score_result.get("recommended_price")
                deal_score.confidence = score_result.get("confidence")
                deal_score.explanation_short = score_result.get("explanation_short")
                deal_score.risks = score_result.get("risks", [])
                deal_score.estimated_sell_days = score_result.get("estimated_sell_days")
                deal_score.margin_score = score_result.get("score_breakdown", {}).get("margin_score")
                deal_score.liquidity_score = score_result.get("score_breakdown", {}).get("liquidity_score")
                deal_score.popularity_score = score_result.get("score_breakdown", {}).get("popularity_score")
                deal_score.scored_at = datetime.utcnow()
                deal.score = deal_score.flip_score
                
                db.commit()
                results["scored"] += 1
                logger.info(f"  -> FlipScore: {deal_score.flip_score}, Margin: {vinted_stat.margin_pct}%")
                
            except Exception as e:
                results["errors"] += 1
                logger.error(f"Error scoring deal {deal.id}: {e}")
                db.rollback()
        
        loop.close()
    finally:
        db.close()
    
    logger.info(f"Rescraping complete: scored={results['scored']}, no_data={results['no_data']}, errors={results['errors']}")
    return results


def score_single_deal_with_vinted(deal_id: int) -> Dict:
    """
    Score un deal unique avec Vinted (appelé via RQ pour les deals qualifiés).
    """
    import asyncio
    from app.db.session import SessionLocal
    from app.models.deal import Deal
    from app.models.vinted_stats import VintedStats
    from app.models.deal_score import DealScore
    from app.services.vinted_service import get_vinted_stats_for_deal
    
    logger.info(f"Starting Vinted scoring for deal {deal_id}")
    
    db = SessionLocal()
    try:
        deal = db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            return {"deal_id": deal_id, "status": "not_found"}
        
        # Récupérer les stats Vinted
        try:
            stats = asyncio.run(get_vinted_stats_for_deal(deal.title, deal.brand, deal.price))
        except Exception as e:
            logger.warning(f"Vinted scrape error for deal {deal_id}: {e}")
            stats = None
        
        if not stats or stats.get("nb_listings", 0) == 0:
            # Créer un enregistrement vide pour marquer comme traité
            vinted_stat = db.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
            if not vinted_stat:
                vinted_stat = VintedStats(deal_id=deal_id, nb_listings=0)
                db.add(vinted_stat)
                db.commit()
            return {"deal_id": deal_id, "status": "no_listings", "nb_listings": 0}
        
        # Sauvegarder les stats Vinted
        vinted_stat = db.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        if not vinted_stat:
            vinted_stat = VintedStats(deal_id=deal_id)
            db.add(vinted_stat)
        
        vinted_stat.nb_listings = stats.get("nb_listings", 0)
        vinted_stat.price_min = stats.get("price_min")
        vinted_stat.price_max = stats.get("price_max")
        vinted_stat.price_median = stats.get("price_median")
        vinted_stat.margin_pct = stats.get("margin_pct")
        vinted_stat.margin_euro = stats.get("margin_euro")
        vinted_stat.liquidity_score = stats.get("liquidity_score")
        
        db.commit()
        
        logger.info(f"Vinted scoring completed for deal {deal_id}: {stats.get('nb_listings')} listings, margin: {stats.get('margin_pct')}%")
        
        return {
            "deal_id": deal_id,
            "status": "scored",
            "nb_listings": stats.get("nb_listings", 0),
            "margin_pct": stats.get("margin_pct", 0),
            "price_median": stats.get("price_median", 0),
        }
        
    except Exception as e:
        logger.error(f"Error scoring deal {deal_id} with Vinted: {e}")
        db.rollback()
        return {"deal_id": deal_id, "status": "error", "error": str(e)}
    finally:
        db.close()
