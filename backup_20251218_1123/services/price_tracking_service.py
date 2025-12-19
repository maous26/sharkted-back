"""
Price Tracking Service - Détection de drops et tracking historique.

Ce service:
1. Enregistre chaque observation de prix
2. Calcule les stats agrégées (min/max/avg sur 7j et 30j)
3. Détecte les price drops (signal le plus fiable)
4. Calcule la volatilité pour ajuster les seuils
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from statistics import median, stdev, mean

from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.price_history import PriceHistory, DealPriceStats
from app.models.deal import Deal

logger = get_logger(__name__)

# Seuils de détection de drop
DROP_THRESHOLD_VOLATILE = 0.10  # 10% pour produits volatils
DROP_THRESHOLD_STABLE = 0.15   # 15% pour produits stables
DROP_THRESHOLD_DEFAULT = 0.12  # 12% par défaut

# Seuil de volatilité pour classifier un produit
VOLATILITY_THRESHOLD = 0.15  # CV > 15% = volatil


def record_price_observation(
    deal_id: int,
    price: float,
    original_price: Optional[float] = None,
    source_url: Optional[str] = None,
    session: Optional[Session] = None,
) -> Tuple[bool, Optional[float]]:
    """
    Enregistre une observation de prix et détecte les drops.
    
    Returns:
        Tuple (is_drop, drop_percent) - True si drop détecté
    """
    close_session = False
    if session is None:
        session = SessionLocal()
        close_session = True
    
    try:
        # 1. Récupérer ou créer les stats du deal
        stats = session.query(DealPriceStats).filter(
            DealPriceStats.deal_id == deal_id
        ).first()
        
        is_new = stats is None
        is_drop = False
        drop_percent = None
        
        if is_new:
            # Premier enregistrement
            stats = DealPriceStats(
                deal_id=deal_id,
                current_price=price,
                previous_price=None,
                min_price_30d=price,
                max_price_30d=price,
                avg_price_30d=price,
                observations_count=1,
                first_seen_at=datetime.utcnow(),
            )
            session.add(stats)
        else:
            # Mise à jour
            old_price = stats.current_price
            
            # Vérifier si le prix a changé
            if abs(price - old_price) > 0.01:
                stats.previous_price = old_price
                stats.current_price = price
                stats.price_changes_count += 1
                
                # Détecter un drop
                is_drop, drop_percent = _detect_drop(price, stats)
                
                if is_drop:
                    stats.is_price_drop = 1
                    stats.drop_percent = drop_percent
                    stats.drop_detected_at = datetime.utcnow()
                    logger.info(
                        f"Price drop detected!",
                        deal_id=deal_id,
                        old_price=old_price,
                        new_price=price,
                        drop_percent=drop_percent,
                    )
            
            stats.observations_count += 1
            stats.last_updated_at = datetime.utcnow()
        
        # 2. Ajouter à l'historique
        history = PriceHistory(
            deal_id=deal_id,
            price=price,
            original_price=original_price,
            source_url=source_url,
            observed_at=datetime.utcnow(),
        )
        session.add(history)
        
        # 3. Recalculer les stats périodiquement (tous les 5 observations)
        if stats.observations_count % 5 == 0 or is_new:
            _update_price_stats(deal_id, stats, session)
        
        session.commit()
        return is_drop, drop_percent
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error recording price: {e}", deal_id=deal_id)
        raise
    finally:
        if close_session:
            session.close()


def _detect_drop(current_price: float, stats: DealPriceStats) -> Tuple[bool, Optional[float]]:
    """
    Détecte si le prix actuel représente un drop significatif.
    
    Logique:
    1. Compare au min_price_30d (référence historique)
    2. Compare au previous_price (drop récent)
    3. Ajuste le seuil selon la volatilité
    """
    # Déterminer le seuil selon la volatilité
    if stats.price_volatility and stats.price_volatility > VOLATILITY_THRESHOLD:
        threshold = DROP_THRESHOLD_VOLATILE
    elif stats.price_volatility and stats.price_volatility < 0.05:
        threshold = DROP_THRESHOLD_STABLE
    else:
        threshold = DROP_THRESHOLD_DEFAULT
    
    # Signal 1: Drop vs min_price_30d
    if stats.min_price_30d and stats.min_price_30d > 0:
        if current_price <= stats.min_price_30d * (1 - threshold):
            drop_pct = (1 - current_price / stats.min_price_30d) * 100
            return True, round(drop_pct, 1)
    
    # Signal 2: Drop vs previous_price (drop récent)
    if stats.previous_price and stats.previous_price > 0:
        if current_price <= stats.previous_price * (1 - threshold):
            drop_pct = (1 - current_price / stats.previous_price) * 100
            return True, round(drop_pct, 1)
    
    return False, None


def _update_price_stats(deal_id: int, stats: DealPriceStats, session: Session):
    """Recalcule les stats agrégées depuis l'historique."""
    now = datetime.utcnow()
    
    # Stats 30 jours
    cutoff_30d = now - timedelta(days=30)
    prices_30d = session.query(PriceHistory.price).filter(
        PriceHistory.deal_id == deal_id,
        PriceHistory.observed_at >= cutoff_30d
    ).all()
    prices_30d = [p[0] for p in prices_30d if p[0]]
    
    if prices_30d:
        stats.min_price_30d = min(prices_30d)
        stats.max_price_30d = max(prices_30d)
        stats.avg_price_30d = round(mean(prices_30d), 2)
        stats.median_price_30d = round(median(prices_30d), 2)
        
        # Volatilité (coefficient de variation)
        if len(prices_30d) > 1 and stats.avg_price_30d > 0:
            try:
                stats.price_volatility = round(stdev(prices_30d) / stats.avg_price_30d, 3)
            except:
                stats.price_volatility = 0
        
        # Tendance
        if len(prices_30d) >= 3:
            recent = prices_30d[-3:]
            if recent[-1] < recent[0] * 0.95:
                stats.price_trend = "down"
            elif recent[-1] > recent[0] * 1.05:
                stats.price_trend = "up"
            else:
                stats.price_trend = "stable"
    
    # Stats 7 jours
    cutoff_7d = now - timedelta(days=7)
    prices_7d = session.query(PriceHistory.price).filter(
        PriceHistory.deal_id == deal_id,
        PriceHistory.observed_at >= cutoff_7d
    ).all()
    prices_7d = [p[0] for p in prices_7d if p[0]]
    
    if prices_7d:
        stats.min_price_7d = min(prices_7d)
        stats.max_price_7d = max(prices_7d)


def get_price_drops(
    min_drop_percent: float = 10.0,
    limit: int = 50,
    hours: int = 24,
) -> List[Dict]:
    """
    Récupère les deals avec des drops de prix récents.
    
    Args:
        min_drop_percent: Drop minimum en %
        limit: Nombre max de résultats
        hours: Fenêtre de temps en heures
    """
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        results = session.query(
            DealPriceStats, Deal
        ).join(
            Deal, Deal.id == DealPriceStats.deal_id
        ).filter(
            DealPriceStats.is_price_drop == 1,
            DealPriceStats.drop_percent >= min_drop_percent,
            DealPriceStats.drop_detected_at >= cutoff,
        ).order_by(
            DealPriceStats.drop_percent.desc()
        ).limit(limit).all()
        
        return [
            {
                "deal_id": stats.deal_id,
                "title": deal.title,
                "source": deal.source,
                "url": deal.url,
                "image_url": deal.image_url,
                "current_price": stats.current_price,
                "previous_price": stats.previous_price,
                "min_price_30d": stats.min_price_30d,
                "drop_percent": stats.drop_percent,
                "drop_detected_at": stats.drop_detected_at.isoformat() if stats.drop_detected_at else None,
                "price_trend": stats.price_trend,
                "volatility": stats.price_volatility,
            }
            for stats, deal in results
        ]
    finally:
        session.close()


def get_deals_to_watch(limit: int = 100) -> List[int]:
    """
    Retourne les IDs des deals à surveiller en priorité (watchlist).
    
    Critères:
    - Tendance baissière
    - Proche du min historique
    - Volatilité élevée (changements fréquents)
    """
    session = SessionLocal()
    try:
        # Deals avec tendance baissière ou proches du min
        results = session.query(DealPriceStats.deal_id).filter(
            and_(
                DealPriceStats.price_trend == "down",
                DealPriceStats.observations_count >= 3,
            )
        ).union(
            # Deals proches de leur min historique (< 110% du min)
            session.query(DealPriceStats.deal_id).filter(
                DealPriceStats.current_price <= DealPriceStats.min_price_30d * 1.10,
                DealPriceStats.min_price_30d.isnot(None),
            )
        ).union(
            # Deals volatils (opportunités fréquentes)
            session.query(DealPriceStats.deal_id).filter(
                DealPriceStats.price_volatility >= VOLATILITY_THRESHOLD,
            )
        ).limit(limit).all()
        
        return [r[0] for r in results]
    finally:
        session.close()


def cleanup_old_history(days: int = 90):
    """Supprime l'historique de prix de plus de X jours."""
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = session.query(PriceHistory).filter(
            PriceHistory.observed_at < cutoff
        ).delete()
        session.commit()
        logger.info(f"Cleaned up {deleted} old price history records")
        return deleted
    finally:
        session.close()
