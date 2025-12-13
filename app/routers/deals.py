"""
Deals Router - Consultation des deals persistés.
Endpoints: /v1/deals/*
"""
from fastapi import APIRouter, HTTPException

from app.services.deal_service import (
    get_deal,
    get_deals_by_source,
    get_source_stats,
    get_all_deals,
    get_recent_deals,
)

router = APIRouter(prefix="/v1/deals", tags=["deals"])


@router.get("")
def list_all_deals(
    limit: int = 100,
    offset: int = 0,
    source: str = None,
    min_price: float = None,
    max_price: float = None,
    currency: str = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
):
    """
    Liste tous les deals avec filtres et tri.

    - **sort_by**: price, last_seen_at, first_seen_at
    - **sort_order**: asc, desc
    - **currency**: EUR, GBP, USD
    """
    return get_all_deals(
        limit=limit,
        offset=offset,
        source=source,
        min_price=min_price,
        max_price=max_price,
        currency=currency,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/recent")
def list_recent_deals(hours: int = 24, limit: int = 50):
    """Deals vus dans les dernières X heures."""
    deals = get_recent_deals(hours=hours, limit=limit)
    return {"count": len(deals), "hours": hours, "deals": deals}


@router.get("/stats")
def deals_stats():
    """Statistiques par source."""
    return get_source_stats()


@router.get("/{source}")
def list_deals_by_source(
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: float = None,
    max_price: float = None,
):
    """Liste les deals d'une source avec filtres optionnels."""
    deals = get_deals_by_source(
        source=source,
        limit=limit,
        offset=offset,
        min_price=min_price,
        max_price=max_price,
    )
    return {"source": source, "count": len(deals), "deals": deals}


@router.get("/{source}/{external_id}")
def get_deal_detail(source: str, external_id: str):
    """Récupère un deal spécifique."""
    deal = get_deal(source, external_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal
