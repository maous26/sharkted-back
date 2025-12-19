"""
Service de persistance des deals.
Point d'entrée unique pour sauvegarder les items collectés.
"""
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, asc

from app.db.session import SessionLocal
from app.models.deal import Deal
from app.normalizers.item import DealItem
from app.repositories.deal_repository import DealRepository


@contextmanager
def get_db_session():
    """Context manager pour obtenir une session DB."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def persist_deal(item: DealItem) -> Dict[str, Any]:
    with get_db_session() as session:
        repo = DealRepository(session)
        existing = repo.get_by_source_and_id(item.source, item.external_id)
        was_existing = existing is not None
        old_price = existing.price if existing else None
        deal = repo.upsert(item)

        return {
            "id": deal.id,
            "source": deal.source,
            "external_id": deal.external_id,
            "action": "updated" if was_existing else "created",
            "price_changed": was_existing and old_price != deal.price,
            "old_price": old_price if was_existing else None,
            "new_price": deal.price,
        }


def persist_deals_batch(items: List[DealItem]) -> List[Dict[str, Any]]:
    results = []
    with get_db_session() as session:
        repo = DealRepository(session)
        for item in items:
            existing = repo.get_by_source_and_id(item.source, item.external_id)
            was_existing = existing is not None
            old_price = existing.price if existing else None
            deal = repo.upsert(item)
            results.append({
                "id": deal.id,
                "source": deal.source,
                "external_id": deal.external_id,
                "action": "updated" if was_existing else "created",
                "price_changed": was_existing and old_price != deal.price,
            })
    return results


def get_deal(source: str, external_id: str) -> Optional[Dict[str, Any]]:
    with get_db_session() as session:
        repo = DealRepository(session)
        deal = repo.get_by_source_and_id(source, external_id)
        if deal:
            return _deal_to_api_dict(deal)
        return None


def get_deals_by_source(
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> List[Dict[str, Any]]:
    with get_db_session() as session:
        repo = DealRepository(session)
        deals = repo.get_by_source(
            source=source,
            limit=limit,
            offset=offset,
            min_price=min_price,
            max_price=max_price,
        )
        return [_deal_to_api_dict(d) for d in deals]


def get_source_stats() -> Dict[str, int]:
    with get_db_session() as session:
        from sqlalchemy import func
        result = session.query(
            Deal.source,
            func.count(Deal.id)
        ).group_by(Deal.source).all()
        return {source: count for source, count in result}


def get_all_deals(
    limit: int = 100,
    offset: int = 0,
    source: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    currency: Optional[str] = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
) -> Dict[str, Any]:
    with get_db_session() as session:
        query = session.query(Deal).options(
            joinedload(Deal.vinted_stats),
            joinedload(Deal.score_data)
        ).filter(Deal.in_stock == True)

        if source:
            query = query.filter(Deal.source == source)
        if min_price is not None:
            query = query.filter(Deal.price >= min_price)
        if max_price is not None:
            query = query.filter(Deal.price <= max_price)
        if currency:
            query = query.filter(Deal.currency == currency)

        total = query.count()

        sort_column = getattr(Deal, sort_by, Deal.last_seen_at)
        if sort_order == "asc":
            query = query.order_by(asc(sort_column))
        else:
            query = query.order_by(desc(sort_column))

        deals = query.offset(offset).limit(limit).all()

        return {
            "deals": [_deal_to_api_dict(d) for d in deals],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(deals) < total,
        }


def get_recent_deals(hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
    with get_db_session() as session:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        deals = (
            session.query(Deal)
            .options(joinedload(Deal.vinted_stats), joinedload(Deal.score_data))
            .filter(Deal.last_seen_at >= cutoff)
            .filter(Deal.in_stock == True)
            .order_by(Deal.last_seen_at.desc())
            .limit(limit)
            .all()
        )
        return [_deal_to_api_dict(d) for d in deals]


def _deal_to_api_dict(deal: Deal) -> Dict[str, Any]:
    """Convertit un Deal en dict format API (compatible frontend)."""
    result = {
        "id": str(deal.id),
        "product_name": deal.title,
        "brand": deal.brand or deal.seller_name,
        "model": getattr(deal, 'model', None),
        "category": getattr(deal, 'category', None),
        "color": getattr(deal, 'color', None),
        "gender": getattr(deal, 'gender', None),
        "original_price": deal.original_price,
        "sale_price": deal.price,
        "discount_pct": deal.discount_percent,
        "product_url": deal.url,
        "image_url": deal.image_url,
        "sizes_available": getattr(deal, 'sizes_available', None),
        "stock_available": deal.in_stock,
        "source_name": deal.source,
        "detected_at": deal.first_seen_at.isoformat() if deal.first_seen_at else None,
    }
    
    # Ajouter les stats Vinted si disponibles
    if hasattr(deal, 'vinted_stats') and deal.vinted_stats:
        vs = deal.vinted_stats
        result["vinted_stats"] = {
            "nb_listings": vs.nb_listings,
            "price_min": vs.price_min,
            "price_max": vs.price_max,
            "price_median": vs.price_median,
            "margin_euro": vs.margin_euro,
            "margin_pct": vs.margin_pct,
            "liquidity_score": vs.liquidity_score,
        }
    else:
        result["vinted_stats"] = None
    
    # Ajouter le score si disponible
    if hasattr(deal, 'score_data') and deal.score_data:
        sd = deal.score_data
        result["score"] = {
            "flip_score": sd.flip_score,
            "recommended_action": sd.recommended_action,
            "recommended_price": sd.recommended_price,
            "confidence": sd.confidence,
            "explanation_short": sd.explanation_short,
            "risks": sd.risks,
            "estimated_sell_days": sd.estimated_sell_days,
        }
    else:
        result["score"] = None
    
    return result
