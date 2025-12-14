"""
Deals Router - Consultation des deals persistés.
Endpoints: /v1/deals/*
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta
from typing import Optional

from app.services.deal_service import (
    get_deal,
    get_deals_by_source,
    get_source_stats,
    get_all_deals,
    get_recent_deals,
    get_db_session,
)
from app.models.deal import Deal
from sqlalchemy import func

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
    """Statistiques globales."""
    with get_db_session() as session:
        # Total deals
        total = session.query(func.count(Deal.id)).scalar() or 0

        # Active deals (in stock)
        active = session.query(func.count(Deal.id)).filter(Deal.in_stock == True).scalar() or 0

        # Deals added today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = session.query(func.count(Deal.id)).filter(Deal.first_seen_at >= today_start).scalar() or 0

        # Average price
        avg_price = session.query(func.avg(Deal.price)).filter(Deal.in_stock == True).scalar() or 0

        # By source
        by_source = dict(session.query(Deal.source, func.count(Deal.id)).group_by(Deal.source).all())

        return {
            "total_deals": total,
            "active_deals": active,
            "deals_today": today_count,
            "average_price": round(float(avg_price), 2),
            "by_source": by_source,
        }


@router.get("/stats/brands")
def deals_stats_brands(limit: int = Query(10, ge=1, le=50)):
    """Top marques par nombre de deals."""
    with get_db_session() as session:
        # Group by source as proxy for brand
        results = session.query(
            Deal.source,
            func.count(Deal.id).label("count"),
            func.avg(Deal.score).label("avg_score"),
            func.avg(Deal.discount_percent).label("avg_margin")
        ).filter(Deal.in_stock == True).group_by(Deal.source).order_by(func.count(Deal.id).desc()).limit(limit).all()

        # Return array format expected by frontend
        return [
            {
                "brand": source.capitalize(),
                "deal_count": count,
                "avg_flip_score": float(avg_score or 0),
                "avg_margin_pct": float(avg_margin or 0)
            }
            for source, count, avg_score, avg_margin in results
        ]


@router.get("/stats/categories")
def deals_stats_categories():
    """Statistiques par catégorie."""
    # Return stats by source as proxy for category
    with get_db_session() as session:
        results = session.query(
            Deal.source,
            func.count(Deal.id).label("count"),
            func.avg(Deal.score).label("avg_score"),
            func.avg(Deal.discount_percent).label("avg_margin")
        ).filter(Deal.in_stock == True).group_by(Deal.source).all()

        # Return array format expected by frontend
        return [
            {
                "category": source.capitalize(),
                "deal_count": count,
                "avg_flip_score": float(avg_score or 0),
                "avg_margin_pct": float(avg_margin or 0)
            }
            for source, count, avg_score, avg_margin in results
        ]


@router.get("/stats/trends")
def deals_stats_trends(days: int = Query(30, ge=1, le=90)):
    """Tendances des deals sur X jours."""
    with get_db_session() as session:
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Group by date
        results = session.query(
            func.date(Deal.first_seen_at).label("date"),
            func.count(Deal.id).label("count")
        ).filter(Deal.first_seen_at >= cutoff).group_by(func.date(Deal.first_seen_at)).order_by(func.date(Deal.first_seen_at)).all()

        # Return format expected by frontend: {data: [{date, value}]}
        data = [
            {"date": str(date), "value": count}
            for date, count in results
        ]

        return {"data": data, "days": days}


@router.get("/stats/score-distribution")
def deals_stats_score_distribution():
    """Distribution des scores des deals."""
    with get_db_session() as session:
        # Group scores into ranges
        ranges = [
            (0, 20, "0-20"),
            (20, 40, "20-40"),
            (40, 60, "40-60"),
            (60, 80, "60-80"),
            (80, 100, "80-100"),
        ]

        # Return array format expected by frontend
        distribution = []
        for min_score, max_score, label in ranges:
            count = session.query(func.count(Deal.id)).filter(
                Deal.in_stock == True,
                Deal.score >= min_score,
                Deal.score < max_score if max_score < 100 else Deal.score <= 100
            ).scalar() or 0
            distribution.append({"range": label, "count": count})

        return distribution


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
