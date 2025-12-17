"""
Jobs de scoring StockX - Calcul des marges StockX pour tous les deals.

Ce module score TOUS les deals avec StockX (pas de restriction sniper)
car StockX ne nécessite pas de web unlocker.
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import text
import os

from app.core.logging import get_logger, set_trace_id
from app.db.session import SessionLocal
from app.models.deal import Deal
from app.models.stockx_stats import StockXStats
from app.models.vinted_stats import VintedStats
from app.services.stockx_service import get_stockx_stats_for_deal

logger = get_logger(__name__)


def score_deal_with_stockx(deal_id: int) -> Dict:
    """
    Score un deal avec StockX.
    
    Returns:
        Dict avec le résultat du scoring
    """
    session = SessionLocal()
    try:
        deal = session.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            return {"deal_id": deal_id, "status": "not_found"}
        
        logger.info(f"StockX scoring for deal {deal_id}: {deal.title[:50]}")
        
        # Récupérer les stats StockX
        stockx_data = get_stockx_stats_for_deal(
            product_name=deal.title,
            brand=deal.brand or deal.seller_name,
            sale_price=deal.price
        )
        
        if stockx_data.get("error"):
            logger.warning(f"StockX error for deal {deal_id}: {stockx_data.get(error)}")
            return {
                "deal_id": deal_id,
                "status": "stockx_error",
                "error": stockx_data.get("error")
            }
        
        # Sauvegarder les stats StockX
        existing_stockx = session.query(StockXStats).filter(StockXStats.deal_id == deal_id).first()
        
        if existing_stockx:
            # Mise à jour
            existing_stockx.product_name = stockx_data.get("product_name")
            existing_stockx.product_url = stockx_data.get("product_url")
            existing_stockx.image_url = stockx_data.get("image_url")
            existing_stockx.lowest_ask = stockx_data.get("lowest_ask", 0)
            existing_stockx.highest_bid = stockx_data.get("highest_bid", 0)
            existing_stockx.last_sale = stockx_data.get("last_sale", 0)
            existing_stockx.sales_last_72h = stockx_data.get("sales_last_72h", 0)
            existing_stockx.retail_price = stockx_data.get("retail_price", 0)
            existing_stockx.volatility = stockx_data.get("volatility", 0)
            existing_stockx.price_premium = stockx_data.get("price_premium", 0)
            existing_stockx.margin_euro = stockx_data.get("margin_euro", 0)
            existing_stockx.margin_pct = stockx_data.get("margin_pct", 0)
            existing_stockx.liquidity_score = stockx_data.get("liquidity_score", 0)
            existing_stockx.updated_at = datetime.utcnow()
        else:
            # Création
            stockx_stats = StockXStats(
                deal_id=deal_id,
                product_name=stockx_data.get("product_name"),
                product_url=stockx_data.get("product_url"),
                image_url=stockx_data.get("image_url"),
                lowest_ask=stockx_data.get("lowest_ask", 0),
                highest_bid=stockx_data.get("highest_bid", 0),
                last_sale=stockx_data.get("last_sale", 0),
                sales_last_72h=stockx_data.get("sales_last_72h", 0),
                retail_price=stockx_data.get("retail_price", 0),
                volatility=stockx_data.get("volatility", 0),
                price_premium=stockx_data.get("price_premium", 0),
                margin_euro=stockx_data.get("margin_euro", 0),
                margin_pct=stockx_data.get("margin_pct", 0),
                liquidity_score=stockx_data.get("liquidity_score", 0),
            )
            session.add(stockx_stats)
        
        # Si marge StockX positive mais pas de marge Vinted, on met à jour Vinted avec estimation
        vinted_stats = session.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        stockx_margin = stockx_data.get("margin_pct", 0)
        
        if stockx_margin > 0 and (not vinted_stats or (vinted_stats.margin_pct or 0) <= 0):
            # Créer/Mettre à jour vinted_stats avec estimation basée sur StockX
            # Vinted généralement 10-20% moins cher que StockX
            estimated_vinted_margin = stockx_margin * 0.85  # 15% de réduction
            
            if not vinted_stats:
                vinted_stats = VintedStats(
                    deal_id=deal_id,
                    nb_listings=0,
                    price_median=stockx_data.get("last_sale", 0) * 0.85,
                    margin_euro=stockx_data.get("margin_euro", 0) * 0.85,
                    margin_pct=estimated_vinted_margin,
                    liquidity_score=stockx_data.get("liquidity_score", 0),
                    search_query=f"[StockX estimate] {deal.title[:50]}"
                )
                session.add(vinted_stats)
            elif vinted_stats.margin_pct is None or vinted_stats.margin_pct <= 0:
                vinted_stats.margin_pct = estimated_vinted_margin
                vinted_stats.margin_euro = stockx_data.get("margin_euro", 0) * 0.85
                vinted_stats.price_median = stockx_data.get("last_sale", 0) * 0.85
        
        session.commit()
        
        logger.info(
            f"StockX scored",
            deal_id=deal_id,
            title=deal.title[:40],
            margin_pct=stockx_data.get("margin_pct", 0),
            lowest_ask=stockx_data.get("lowest_ask", 0)
        )
        
        return {
            "deal_id": deal_id,
            "status": "scored",
            "stockx_margin_pct": stockx_data.get("margin_pct", 0),
            "lowest_ask": stockx_data.get("lowest_ask", 0),
            "last_sale": stockx_data.get("last_sale", 0),
        }
        
    except Exception as e:
        logger.error(f"Failed to score deal {deal_id} with StockX: {e}")
        session.rollback()
        return {
            "deal_id": deal_id,
            "status": "error",
            "error": str(e)[:200],
        }
    finally:
        session.close()


def score_all_deals_stockx(limit: int = 50, skip_existing: bool = True) -> Dict:
    """
    Score tous les deals avec StockX.
    
    Args:
        limit: Nombre max de deals à scorer
        skip_existing: Si True, ignore les deals déjà scorés avec StockX
    
    Returns:
        Dict avec les résultats
    """
    trace_id = set_trace_id()
    start_time = time.perf_counter()
    
    logger.info(f"Starting StockX scoring", limit=limit, skip_existing=skip_existing)
    
    session = SessionLocal()
    try:
        if skip_existing:
            # Deals sans stats StockX
            query = text("""
                SELECT d.id 
                FROM deals d 
                LEFT JOIN stockx_stats ss ON d.id = ss.deal_id 
                WHERE ss.id IS NULL AND d.in_stock = true
                ORDER BY d.first_seen_at DESC 
                LIMIT :limit
            """)
        else:
            # Tous les deals actifs
            query = text("""
                SELECT d.id 
                FROM deals d 
                WHERE d.in_stock = true
                ORDER BY d.first_seen_at DESC 
                LIMIT :limit
            """)
        
        result = session.execute(query, {"limit": limit})
        deal_ids = [row[0] for row in result]
        
        logger.info(f"Found {len(deal_ids)} deals to score with StockX")
        
        results = {
            "scored": 0,
            "errors": 0,
            "skipped": 0,
            "details": []
        }
        
        for deal_id in deal_ids:
            # Rate limiting pour éviter de surcharger StockX
            time.sleep(0.5)
            
            result = score_deal_with_stockx(deal_id)
            results["details"].append(result)
            
            if result["status"] == "scored":
                results["scored"] += 1
            elif result["status"] == "stockx_error":
                results["skipped"] += 1
            else:
                results["errors"] += 1
        
        duration = time.perf_counter() - start_time
        logger.info(
            f"StockX scoring completed",
            scored=results["scored"],
            errors=results["errors"],
            skipped=results["skipped"],
            duration_sec=round(duration, 2)
        )
        
        return results
        
    except Exception as e:
        logger.error(f"StockX scoring job failed: {e}")
        return {"error": str(e)}
    finally:
        session.close()


def scheduled_stockx_scoring():
    """
    Job planifié pour scorer les deals avec StockX.
    Lancé toutes les heures via RQ Scheduler.
    """
    logger.info("=== Scheduled StockX scoring START ===")
    result = score_all_deals_stockx(limit=30, skip_existing=True)
    logger.info(f"=== Scheduled StockX scoring END: {result.get(scored, 0)} deals scored ===")
    return result
