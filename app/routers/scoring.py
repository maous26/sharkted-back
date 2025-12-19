"""
Scoring Router - Scoring et analyse des deals.
Endpoints: /v1/scoring/*
"""
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional
from loguru import logger

from app.db.session import SessionLocal
from app.models.deal import Deal
from app.models.vinted_stats import VintedStats
from app.models.deal_score import DealScore
from app.services.vinted_service import get_vinted_stats_for_deal
from app.services.scoring_service import score_deal


router = APIRouter(prefix="/v1/scoring", tags=["scoring"])


@router.post("/score/{deal_id}")
async def score_single_deal(deal_id: int):
    """
    Score un deal spécifique avec stats Vinted.
    
    Process:
    1. Récupère le deal
    2. Recherche sur Vinted
    3. Calcule les stats de marge
    4. Calcule le FlipScore
    5. Sauvegarde les résultats
    """
    session = SessionLocal()
    try:
        # Get deal
        deal = session.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        logger.info(f"Scoring deal {deal_id}: {deal.title}")
        
        # Get Vinted stats
        vinted_data = await get_vinted_stats_for_deal(
            product_name=deal.title,
            brand=deal.brand or deal.seller_name,
            sale_price=deal.price,
                    sizes_available=deal.sizes_available
        )
        
        # Save Vinted stats
        existing_vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        if existing_vinted:
            # Update
            for key, value in vinted_data.items():
                if key != 'sample_listings' and hasattr(existing_vinted, key):
                    setattr(existing_vinted, key, value)
            existing_vinted.sample_listings = vinted_data.get('sample_listings', [])
        else:
            # Create
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
        
        # Calculate score
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
        
        # Save score
        existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
        if existing_score:
            for key, value in score_result.items():
                if hasattr(existing_score, key):
                    setattr(existing_score, key, value)
        else:
            deal_score = DealScore(
                deal_id=deal_id,
                flip_score=score_result['flip_score'],
                popularity_score=score_result['popularity_score'],
                liquidity_score=score_result['liquidity_score'],
                margin_score=score_result['margin_score'],
                score_breakdown=score_result.get('score_breakdown'),
                recommended_action=score_result['recommended_action'],
                recommended_price=score_result.get('recommended_price'),
                confidence=score_result['confidence'],
                explanation=score_result['explanation'],
                explanation_short=score_result['explanation_short'],
                risks=score_result.get('risks', []),
                estimated_sell_days=score_result.get('estimated_sell_days'),
                model_version=score_result.get('model_version', 'rules_v1')
            )
            session.add(deal_score)
        
        # Update deal score
        deal.score = score_result['flip_score']
        
        session.commit()
        
        logger.info(f"Deal {deal_id} scored: FlipScore={score_result['flip_score']}, Action={score_result['recommended_action']}")
        
        return {
            "deal_id": deal_id,
            "vinted_stats": vinted_data,
            "score": score_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"Error scoring deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/score-batch")
async def score_batch_deals(limit: int = 10, min_listings: int = 0):
    """
    Score les deals non scorés en batch.
    
    - limit: Nombre max de deals à scorer
    - min_listings: Nombre min d'annonces Vinted pour scorer
    """
    session = SessionLocal()
    results = []
    
    try:
        # Get unscored deals
        deals = session.query(Deal).outerjoin(DealScore).filter(
            DealScore.id == None,
            Deal.in_stock == True
        ).limit(limit).all()
        
        logger.info(f"Scoring {len(deals)} deals...")
        
        for deal in deals:
            try:
                # Get Vinted stats
                vinted_data = await get_vinted_stats_for_deal(
                    product_name=deal.title,
                    brand=deal.brand or deal.seller_name,
                    sale_price=deal.price,
                    sizes_available=deal.sizes_available
                )
                
                if vinted_data.get('nb_listings', 0) < min_listings:
                    results.append({
                        'deal_id': deal.id,
                        'status': 'skipped',
                        'reason': f"Only {vinted_data.get('nb_listings', 0)} listings"
                    })
                    continue
                
                # Save Vinted stats
                vinted_stats = VintedStats(
                    deal_id=deal.id,
                    nb_listings=vinted_data.get('nb_listings', 0),
                    price_min=vinted_data.get('price_min'),
                    price_max=vinted_data.get('price_max'),
                    price_median=vinted_data.get('price_median'),
                    margin_euro=vinted_data.get('margin_euro'),
                    margin_pct=vinted_data.get('margin_pct'),
                    liquidity_score=vinted_data.get('liquidity_score'),
                    sample_listings=vinted_data.get('sample_listings', []),
                    search_query=vinted_data.get('query_used', '')
                )
                session.add(vinted_stats)
                
                # Calculate score
                deal_data = {
                    'product_name': deal.title,
                    'brand': deal.brand or deal.seller_name,
                    'category': deal.category or 'default',
                    'color': deal.color,
                    'discount_percent': deal.discount_percent or 0,
                    'sizes_available': deal.sizes_available,
                }
                
                score_result = await score_deal(deal_data, vinted_data)
                
                # Save score
                deal_score = DealScore(
                    deal_id=deal.id,
                    flip_score=score_result['flip_score'],
                    recommended_action=score_result['recommended_action'],
                    recommended_price=score_result.get('recommended_price'),
                    confidence=score_result['confidence'],
                    explanation_short=score_result['explanation_short'],
                    risks=score_result.get('risks', []),
                    estimated_sell_days=score_result.get('estimated_sell_days'),
                    model_version='rules_v1'
                )
                session.add(deal_score)
                
                deal.score = score_result['flip_score']
                
                results.append({
                    'deal_id': deal.id,
                    'status': 'scored',
                    'flip_score': score_result['flip_score'],
                    'action': score_result['recommended_action']
                })
                
                # Rate limit
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.warning(f"Error scoring deal {deal.id}: {e}")
                results.append({
                    'deal_id': deal.id,
                    'status': 'error',
                    'error': str(e)
                })
        
        session.commit()
        
        scored_count = len([r for r in results if r['status'] == 'scored'])
        logger.info(f"Batch scoring complete: {scored_count}/{len(deals)} deals scored")
        
        return {
            'total_processed': len(results),
            'scored': scored_count,
            'results': results
        }
        
    except Exception as e:
        session.rollback()
        logger.error(f"Batch scoring error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/stats")
async def scoring_stats():
    """Statistiques de scoring."""
    session = SessionLocal()
    try:
        from sqlalchemy import func
        
        total_deals = session.query(func.count(Deal.id)).filter(Deal.in_stock == True).scalar() or 0
        scored_deals = session.query(func.count(DealScore.id)).scalar() or 0
        avg_score = session.query(func.avg(DealScore.flip_score)).scalar() or 0
        
        # By action
        action_counts = dict(
            session.query(DealScore.recommended_action, func.count(DealScore.id))
            .group_by(DealScore.recommended_action)
            .all()
        )
        
        # Score distribution
        excellent = session.query(func.count(DealScore.id)).filter(DealScore.flip_score >= 80).scalar() or 0
        good = session.query(func.count(DealScore.id)).filter(DealScore.flip_score >= 60, DealScore.flip_score < 80).scalar() or 0
        average = session.query(func.count(DealScore.id)).filter(DealScore.flip_score >= 40, DealScore.flip_score < 60).scalar() or 0
        poor = session.query(func.count(DealScore.id)).filter(DealScore.flip_score < 40).scalar() or 0
        
        return {
            'total_deals': total_deals,
            'scored_deals': scored_deals,
            'unscored_deals': total_deals - scored_deals,
            'average_flip_score': round(float(avg_score), 1),
            'by_action': action_counts,
            'score_distribution': {
                'excellent_80_plus': excellent,
                'good_60_80': good,
                'average_40_60': average,
                'poor_below_40': poor
            }
        }
    finally:
        session.close()


@router.post("/vinted-batch")
async def score_vinted_batch(
    limit: int = 20,
    source: Optional[str] = None,
    min_listings: int = 0
):
    """
    Score les deals qui n'ont pas de stats Vinted.

    - limit: Nombre max de deals à scorer
    - source: Filtrer par source (printemps, laredoute, etc.)
    - min_listings: Nombre min d'annonces Vinted pour inclure dans les résultats
    """
    session = SessionLocal()
    results = []
    errors = []

    try:
        # Get deals without Vinted stats
        query = session.query(Deal).outerjoin(VintedStats).filter(
            VintedStats.id == None,
            Deal.in_stock == True
        )

        if source:
            query = query.filter(Deal.source == source)

        deals = query.order_by(Deal.id.desc()).limit(limit).all()

        logger.info(f"Vinted batch: {len(deals)} deals to process (source={source})")

        scored = 0
        for deal in deals:
            try:
                # Get Vinted stats
                vinted_data = await get_vinted_stats_for_deal(
                    product_name=deal.title,
                    brand=deal.brand or deal.seller_name,
                    sale_price=deal.price,
                    sizes_available=deal.sizes_available
                )

                nb_listings = vinted_data.get('nb_listings', 0)

                # Save Vinted stats (even if 0 listings)
                vinted_stats = VintedStats(
                    deal_id=deal.id,
                    nb_listings=nb_listings,
                    price_min=vinted_data.get('price_min'),
                    price_max=vinted_data.get('price_max'),
                    price_avg=vinted_data.get('price_avg'),
                    price_median=vinted_data.get('price_median'),
                    price_p25=vinted_data.get('price_p25'),
                    price_p75=vinted_data.get('price_p75'),
                    margin_euro=vinted_data.get('margin_euro'),
                    margin_pct=vinted_data.get('margin_pct'),
                    liquidity_score=vinted_data.get('liquidity_score'),
                    sample_listings=vinted_data.get('sample_listings', []),
                    search_query=vinted_data.get('query_used', '')
                )
                session.add(vinted_stats)

                # Update deal score if we have listings
                if nb_listings >= min_listings:
                    # Re-score with Vinted data
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

                    # Update existing score
                    existing_score = session.query(DealScore).filter(DealScore.deal_id == deal.id).first()
                    if existing_score:
                        existing_score.flip_score = score_result['flip_score']
                        existing_score.popularity_score = score_result.get('popularity_score')
                        existing_score.liquidity_score = score_result.get('liquidity_score')
                        existing_score.margin_score = score_result.get('margin_score')
                        existing_score.score_breakdown = score_result.get('score_breakdown')
                        existing_score.recommended_action = score_result['recommended_action']
                        existing_score.recommended_price = score_result.get('recommended_price')
                        existing_score.confidence = score_result['confidence']
                        existing_score.explanation = score_result['explanation']
                        existing_score.explanation_short = score_result['explanation_short']
                        existing_score.risks = score_result.get('risks', [])

                    deal.score = score_result['flip_score']
                    scored += 1

                results.append({
                    'deal_id': deal.id,
                    'title': deal.title[:40],
                    'source': deal.source,
                    'vinted_listings': nb_listings,
                    'flip_score': deal.score,
                    'query_used': vinted_data.get('query_used', '')
                })

                session.commit()

            except Exception as e:
                session.rollback()
                errors.append({'deal_id': deal.id, 'error': str(e)[:100]})
                logger.warning(f"Error processing deal {deal.id}: {e}")

        return {
            'processed': len(deals),
            'scored': scored,
            'results': results,
            'errors': errors
        }

    except Exception as e:
        logger.error(f"Batch error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

