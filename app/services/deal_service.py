"""
Service de persistance des deals.
Point d'entrée unique pour sauvegarder les items collectés.
"""
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy.orm import Session
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
    """
    Persiste un deal collecté en base.

    Args:
        item: DealItem normalisé depuis un collector

    Returns:
        Dict avec infos sur l'opération:
        - id: ID du deal en base
        - source: source du deal
        - external_id: ID externe
        - action: "created" ou "updated"
        - price_changed: True si le prix a changé
    """
    with get_db_session() as session:
        repo = DealRepository(session)

        # Check si existait avant
        existing = repo.get_by_source_and_id(item.source, item.external_id)
        was_existing = existing is not None
        old_price = existing.price if existing else None

        # Upsert
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
    """
    Persiste une liste de deals.

    Returns:
        Liste de résultats pour chaque deal
    """
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
    """
    Récupère un deal par sa clé logique.
    """
    with get_db_session() as session:
        repo = DealRepository(session)
        deal = repo.get_by_source_and_id(source, external_id)
        if deal:
            return _deal_to_dict(deal)
        return None


def get_deals_by_source(
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Récupère les deals d'une source.
    """
    with get_db_session() as session:
        repo = DealRepository(session)
        deals = repo.get_by_source(
            source=source,
            limit=limit,
            offset=offset,
            min_price=min_price,
            max_price=max_price,
        )
        return [_deal_to_dict(d) for d in deals]


def get_source_stats() -> Dict[str, int]:
    """
    Statistiques par source.
    """
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
    """
    Récupère tous les deals avec filtres et tri.

    Args:
        limit: Nombre max de résultats
        offset: Offset pour pagination
        source: Filtrer par source (optionnel)
        min_price: Prix minimum (optionnel)
        max_price: Prix maximum (optionnel)
        currency: Filtrer par devise (optionnel)
        sort_by: Champ de tri (price, last_seen_at, first_seen_at)
        sort_order: Ordre (asc, desc)

    Returns:
        Dict avec deals, total, et métadonnées
    """
    with get_db_session() as session:
        query = session.query(Deal).filter(Deal.in_stock == True)

        # Filtres
        if source:
            query = query.filter(Deal.source == source)
        if min_price is not None:
            query = query.filter(Deal.price >= min_price)
        if max_price is not None:
            query = query.filter(Deal.price <= max_price)
        if currency:
            query = query.filter(Deal.currency == currency)

        # Count total avant pagination
        total = query.count()

        # Tri
        sort_column = getattr(Deal, sort_by, Deal.last_seen_at)
        if sort_order == "asc":
            query = query.order_by(asc(sort_column))
        else:
            query = query.order_by(desc(sort_column))

        # Pagination
        deals = query.offset(offset).limit(limit).all()

        return {
            "deals": [_deal_to_dict(d) for d in deals],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(deals) < total,
        }


def get_recent_deals(hours: int = 24, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Récupère les deals vus récemment (fraîcheur).
    """
    with get_db_session() as session:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        deals = (
            session.query(Deal)
            .filter(Deal.last_seen_at >= cutoff)
            .filter(Deal.in_stock == True)
            .order_by(Deal.last_seen_at.desc())
            .limit(limit)
            .all()
        )
        return [_deal_to_dict(d) for d in deals]


def _deal_to_dict(deal: Deal) -> Dict[str, Any]:
    """Convertit un Deal en dict."""
    return {
        "id": deal.id,
        "source": deal.source,
        "external_id": deal.external_id,
        "title": deal.title,
        "price": deal.price,
        "currency": deal.currency,
        "url": deal.url,
        "image_url": deal.image_url,
        "seller_name": deal.seller_name,
        "location": deal.location,
        "original_price": deal.original_price,
        "discount_percent": deal.discount_percent,
        "in_stock": deal.in_stock,
        "score": deal.score,
        "first_seen_at": deal.first_seen_at.isoformat() if deal.first_seen_at else None,
        "last_seen_at": deal.last_seen_at.isoformat() if deal.last_seen_at else None,
        "price_updated_at": deal.price_updated_at.isoformat() if deal.price_updated_at else None,
    }
