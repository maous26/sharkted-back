"""
Instant Scoring Service - Score un deal AVANT insertion en base.

Ce service permet de:
1. Scorer un deal immédiatement lors de la collecte
2. Décider si le deal mérite d'être persisté (score >= seuil)
3. Éviter de charger la base avec des deals de faible qualité
"""
import asyncio
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from loguru import logger

from app.normalizers.item import DealItem
from app.services.vinted_service import get_vinted_stats_for_deal
from app.services.scoring_service import score_deal


# Seuil minimum pour persister un deal
MIN_SCORE_THRESHOLD = 60


async def score_deal_instant(item: DealItem) -> Tuple[Optional[Dict], Optional[Dict], float]:
    """
    Score un deal instantanément AVANT insertion.
    
    Args:
        item: Le DealItem collecté
        
    Returns:
        Tuple (vinted_stats, score_data, flip_score)
        - vinted_stats: Données Vinted (ou None si erreur)
        - score_data: Données de scoring complètes (ou None si erreur)  
        - flip_score: Score numérique (0 si erreur)
    """
    try:
        # 1. Récupérer les stats Vinted
        vinted_data = await get_vinted_stats_for_deal(
            product_name=item.title,
            brand=item.brand or item.seller_name,
            sale_price=item.price
        )
        
        # 2. Préparer les données du deal
        deal_data = {
            'product_name': item.title,
            'brand': item.brand or item.seller_name,
            'model': item.model,
            'category': item.category or 'default',
            'color': item.color,
            'gender': item.gender,
            'discount_percent': item.discount_percent or 0,
            'sizes_available': item.sizes_available,
        }
        
        # 3. Calculer le score
        score_result = await score_deal(deal_data, vinted_data)
        flip_score = score_result.get('flip_score', 0)
        
        logger.debug(
            f"Instant score: {flip_score:.1f} for {item.title[:40]}",
            source=item.source,
            flip_score=flip_score
        )
        
        return vinted_data, score_result, flip_score
        
    except Exception as e:
        logger.warning(f"Instant scoring failed: {e}", title=item.title[:40])
        return None, None, 0


def score_deal_instant_sync(item: DealItem) -> Tuple[Optional[Dict], Optional[Dict], float]:
    """Version synchrone de score_deal_instant."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(score_deal_instant(item))


def should_persist_deal(flip_score: float, threshold: int = MIN_SCORE_THRESHOLD) -> bool:
    """Détermine si un deal doit être persisté en base."""
    return flip_score >= threshold
