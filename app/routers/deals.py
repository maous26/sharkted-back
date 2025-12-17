"""
Deals Router - Consultation des deals persistés.
Endpoints: /v1/deals/*

LIMITATIONS PAR TIER:
- FREEMIUM: 5 deals max (1 top + 4 autres), pas de Vinted stats
- BASIC: Illimité, Vinted scoring
- PREMIUM: Illimité, toutes sources, Vinted scoring
"""
from fastapi import APIRouter, HTTPException, Query, Request
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from jose import jwt, JWTError

from app.services.deal_service import (
    get_deal,
    get_deals_by_source,
    get_source_stats,
    get_all_deals,
    get_recent_deals,
    get_db_session,
)
from app.models.deal import Deal
from app.models.deal_score import DealScore
from app.models.vinted_stats import VintedStats
from app.models.user import User
from app.core.config import JWT_SECRET, JWT_ALGO
from app.core.subscription_tiers import (
    SubscriptionTier,
    get_tier_from_plan,
    get_tier_limits,
    get_allowed_sources,
    can_use_vinted_scoring,
    get_max_deals,
    get_tier_info,
)
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/v1/deals", tags=["deals"])


def get_user_info_from_request(request: Request) -> Tuple[List[str], SubscriptionTier, Optional[str]]:
    """
    Extract user info from token if present.
    Returns (categories, tier, plan) tuple.
    Anonymous users get FREEMIUM tier.
    """
    # Try cookie first, then Authorization header
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        # Anonymous = FREEMIUM
        return [], SubscriptionTier.FREEMIUM, None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        if not email:
            return [], SubscriptionTier.FREEMIUM, None
    except JWTError:
        return [], SubscriptionTier.FREEMIUM, None

    # Get user info from DB
    with get_db_session() as session:
        user = session.query(User).filter(User.email == email).first()
        if not user:
            return [], SubscriptionTier.FREEMIUM, None
        
        # Get categories
        categories = []
        if user.preferences:
            categories = user.preferences.get("categories", [])
        
        # Get tier from plan
        plan = user.plan or "free"
        tier = get_tier_from_plan(plan)
        
        return categories, tier, plan


def _deal_to_frontend_format(deal, score=None, vinted=None, show_vinted=True):
    """Convert deal to frontend expected format."""
    return {
        "id": str(deal.id),
        "product_name": deal.title,
        "brand": deal.brand or deal.seller_name,
        "model": deal.model,
        "category": deal.category,
        "color": deal.color,
        "gender": deal.gender,
        "original_price": deal.original_price,
        "sale_price": deal.price,
        "discount_pct": deal.discount_percent,
        "product_url": deal.url,
        "image_url": deal.image_url,
        "sizes_available": deal.sizes_available or [],
        "stock_available": deal.in_stock,
        "source_name": deal.source,
        "detected_at": deal.first_seen_at.isoformat() if deal.first_seen_at else None,
        # Score data with detailed breakdown
        "score": {
            "flip_score": score.flip_score,
            "margin_score": score.margin_score,
            "liquidity_score": score.liquidity_score,
            "popularity_score": score.popularity_score,
            "score_breakdown": score.score_breakdown or {},
            "recommended_action": score.recommended_action.lower() if score.recommended_action else None,
            "recommended_price": score.recommended_price,
            "confidence": score.confidence,
            "explanation_short": score.explanation_short,
            "risks": score.risks or [],
            "estimated_sell_days": score.estimated_sell_days,
        } if score else None,
        # Vinted stats - ONLY for users with vinted_scoring enabled
        "vinted_stats": {
            "nb_listings": vinted.nb_listings,
            "price_min": vinted.price_min,
            "price_max": vinted.price_max,
            "price_median": vinted.price_median,
            "margin_euro": vinted.margin_euro,
            "margin_pct": vinted.margin_pct,
            "liquidity_score": vinted.liquidity_score,
        } if (vinted and show_vinted) else None,
    }


@router.get("")
def list_all_deals(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    source: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_score: Optional[float] = None,
    min_margin: Optional[float] = None,
    recommended_only: bool = False,
    sort_by: str = Query("detected_at", description="Sort field: detected_at, flip_score, margin_pct, price"),
    sort_order: str = Query("desc", description="Sort order: asc, desc"),
):
    """
    Liste tous les deals avec filtres avancés.
    Filtre automatiquement par catégories utilisateur si connecté.
    
    LIMITATIONS PAR TIER:
    - FREEMIUM: 5 deals max, pas de Vinted stats
    - BASIC: Illimité, Vinted scoring
    - PREMIUM: Illimité, toutes sources
    """
    # Get user info (categories + tier)
    user_categories, tier, plan = get_user_info_from_request(request)
    limits = get_tier_limits(tier)
    allowed_sources = get_allowed_sources(tier)
    
    with get_db_session() as session:
        # Base query with joins
        query = session.query(Deal).outerjoin(
            DealScore, Deal.id == DealScore.deal_id
        ).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        )
        
        # Filter by allowed sources for this tier
        query = query.filter(Deal.source.in_(allowed_sources))
        
        # Apply user category filter from preferences
        if user_categories:
            query = query.filter(
                or_(
                    Deal.category.in_(user_categories),
                    Deal.category.is_(None)
                )
            )
        
        # Apply filters
        if source:
            # Verify source is allowed for this tier
            if source.lower() not in allowed_sources:
                raise HTTPException(
                    status_code=403,
                    detail=f"Source '{source}' requires premium subscription"
                )
            query = query.filter(Deal.source == source)
        
        if brand:
            query = query.filter(
                or_(
                    Deal.brand.ilike(f"%{brand}%"),
                    Deal.seller_name.ilike(f"%{brand}%"),
                    Deal.title.ilike(f"%{brand}%")
                )
            )
        
        if category:
            query = query.filter(Deal.category == category)
        
        if search:
            query = query.filter(Deal.title.ilike(f"%{search}%"))
        
        if min_price is not None:
            query = query.filter(Deal.price >= min_price)
        
        if max_price is not None:
            query = query.filter(Deal.price <= max_price)
        
        if min_score is not None and min_score > 0:
            query = query.filter(DealScore.flip_score >= min_score)
        
        if min_margin is not None and min_margin > 0:
            query = query.filter(VintedStats.margin_pct >= min_margin)
        
        if recommended_only:
            query = query.filter(DealScore.recommended_action == "BUY")
        
        # Only in stock
        query = query.filter(Deal.in_stock == True)
        
        # Must have a score >= 60
        query = query.filter(DealScore.id != None)
        query = query.filter(DealScore.flip_score >= 60)
        
        # Count total before pagination
        total = query.count()
        
        # Apply sorting
        if sort_by == "flip_score":
            sort_col = DealScore.flip_score
        elif sort_by == "margin_pct":
            sort_col = VintedStats.margin_pct
        elif sort_by == "price" or sort_by == "sale_price":
            sort_col = Deal.price
        else:
            sort_col = Deal.first_seen_at
        
        if sort_order == "asc":
            query = query.order_by(sort_col.asc().nullslast())
        else:
            query = query.order_by(sort_col.desc().nullsfirst())
        
        # FREEMIUM LIMIT: Max 5 deals total
        if limits.max_deals is not None:
            effective_per_page = min(per_page, limits.max_deals)
            effective_offset = 0  # Always show first page only
            if page > 1:
                # Freemium can only see first page
                return {
                    "deals": [],
                    "total": min(total, limits.max_deals),
                    "page": page,
                    "per_page": effective_per_page,
                    "pages": 1,
                    "tier": tier.value,
                    "upgrade_required": True,
                    "message": "Upgrade to Basic or Premium for unlimited deals",
                }
        else:
            effective_per_page = per_page
            effective_offset = (page - 1) * per_page
        
        # Apply pagination
        deals = query.offset(effective_offset).limit(effective_per_page).all()
        
        # Build response with score data
        result = []
        show_vinted = limits.vinted_scoring
        
        for deal in deals:
            score = session.query(DealScore).filter(DealScore.deal_id == deal.id).first()
            vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal.id).first()
            result.append(_deal_to_frontend_format(deal, score, vinted, show_vinted=show_vinted))
        
        # Calculate effective total for freemium
        effective_total = total
        if limits.max_deals is not None:
            effective_total = min(total, limits.max_deals)
        
        return {
            "deals": result,
            "total": effective_total,
            "page": page,
            "per_page": effective_per_page,
            "pages": (effective_total + effective_per_page - 1) // effective_per_page if effective_per_page > 0 else 0,
            "tier": tier.value,
            "vinted_enabled": show_vinted,
        }


@router.get("/recent")
def list_recent_deals(hours: int = 24, limit: int = 50):
    """Deals vus dans les dernières X heures."""
    deals = get_recent_deals(hours=hours, limit=limit)
    return {"count": len(deals), "hours": hours, "deals": deals}


@router.get("/stats")
def deals_stats():
    """Statistiques globales."""
    with get_db_session() as session:
        total = session.query(func.count(Deal.id)).scalar() or 0
        active = session.query(func.count(Deal.id)).filter(Deal.in_stock == True).scalar() or 0
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = session.query(func.count(Deal.id)).filter(Deal.first_seen_at >= today_start).scalar() or 0
        avg_price = session.query(func.avg(Deal.price)).filter(Deal.in_stock == True).scalar() or 0
        by_source = dict(session.query(Deal.source, func.count(Deal.id)).group_by(Deal.source).all())
        scored_count = session.query(func.count(DealScore.id)).scalar() or 0
        avg_score = session.query(func.avg(DealScore.flip_score)).scalar() or 0
        buy_count = session.query(func.count(DealScore.id)).filter(DealScore.recommended_action == "BUY").scalar() or 0
        positive_margin = session.query(func.count(VintedStats.id)).filter(VintedStats.margin_pct > 0).scalar() or 0
        avg_margin = session.query(func.avg(VintedStats.margin_pct)).scalar() or 0

        return {
            "total_deals": total,
            "active_deals": active,
            "deals_today": today_count,
            "average_price": round(float(avg_price), 2),
            "by_source": by_source,
            "scored_deals": scored_count,
            "avg_flip_score": round(float(avg_score), 1) if avg_score else 0,
            "top_deals_count": buy_count,
            "total_sources": len(by_source),
            "positive_margin_deals": positive_margin,
            "avg_margin_pct": round(float(avg_margin), 1) if avg_margin else 0,
        }


@router.get("/stats/brands")
def deals_stats_brands(limit: int = Query(10, ge=1, le=50)):
    """Top marques par nombre de deals."""
    with get_db_session() as session:
        results = session.query(
            func.coalesce(Deal.brand, Deal.seller_name).label("brand"),
            func.count(Deal.id).label("count"),
        ).filter(
            Deal.in_stock == True,
        ).group_by(func.coalesce(Deal.brand, Deal.seller_name)).order_by(func.count(Deal.id).desc()).limit(limit).all()

        brand_stats = []
        for brand, count in results:
            if brand:
                avg_score = session.query(func.avg(DealScore.flip_score)).join(
                    Deal, Deal.id == DealScore.deal_id
                ).filter(
                    or_(Deal.brand == brand, Deal.seller_name == brand)
                ).scalar()
                
                avg_margin = session.query(func.avg(VintedStats.margin_pct)).join(
                    Deal, Deal.id == VintedStats.deal_id
                ).filter(
                    or_(Deal.brand == brand, Deal.seller_name == brand)
                ).scalar()
                
                brand_stats.append({
                    "brand": brand,
                    "deal_count": count,
                    "avg_flip_score": round(float(avg_score), 1) if avg_score else 0,
                    "avg_margin_pct": round(float(avg_margin), 1) if avg_margin else 0
                })

        return brand_stats


@router.get("/stats/categories")
def deals_stats_categories():
    """Statistiques par catégorie."""
    with get_db_session() as session:
        results = session.query(
            Deal.source,
            func.count(Deal.id).label("count"),
        ).filter(
            Deal.in_stock == True
        ).group_by(Deal.source).all()

        return [
            {"category": source.capitalize(), "deal_count": count}
            for source, count in results
        ]


@router.get("/stats/trends")
def deals_stats_trends(days: int = Query(30, ge=1, le=90)):
    """Tendances des deals sur X jours."""
    with get_db_session() as session:
        cutoff = datetime.utcnow() - timedelta(days=days)
        results = session.query(
            func.date(Deal.first_seen_at).label("date"),
            func.count(Deal.id).label("count")
        ).filter(Deal.first_seen_at >= cutoff).group_by(func.date(Deal.first_seen_at)).order_by(func.date(Deal.first_seen_at)).all()

        data = [{"date": str(date), "value": count} for date, count in results]
        return {"data": data, "days": days}


@router.get("/stats/score-distribution")
def deals_stats_score_distribution():
    """Distribution des scores des deals."""
    with get_db_session() as session:
        ranges = [
            (0, 20, "0-20"), (20, 40, "20-40"), (40, 60, "40-60"),
            (60, 80, "60-80"), (80, 100, "80-100"),
        ]
        distribution = []
        total = 0
        for min_score, max_score, label in ranges:
            if max_score < 100:
                count = session.query(func.count(DealScore.id)).filter(
                    DealScore.flip_score >= min_score, DealScore.flip_score < max_score
                ).scalar() or 0
            else:
                count = session.query(func.count(DealScore.id)).filter(
                    DealScore.flip_score >= min_score, DealScore.flip_score <= 100
                ).scalar() or 0
            distribution.append({"range_label": label, "count": count})
            total += count

        for item in distribution:
            item["percentage"] = round((item["count"] / total * 100) if total > 0 else 0, 1)
        return distribution


@router.get("/top/recommended")
def get_top_recommended_deals(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    category: Optional[str] = None,
):
    """
    Get top recommended deals by FlipScore.
    
    FREEMIUM: Max 1 top deal, no Vinted stats
    BASIC/PREMIUM: Full access
    """
    user_categories, tier, plan = get_user_info_from_request(request)
    limits = get_tier_limits(tier)
    allowed_sources = get_allowed_sources(tier)
    
    # FREEMIUM: Max 1 top deal
    effective_limit = limit
    if limits.max_top_deals is not None:
        effective_limit = min(limit, limits.max_top_deals)
    
    with get_db_session() as session:
        query = session.query(Deal).outerjoin(
            DealScore, Deal.id == DealScore.deal_id
        ).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        )
        
        # Filter by allowed sources
        query = query.filter(Deal.source.in_(allowed_sources))
        
        if user_categories:
            query = query.filter(
                or_(
                    Deal.category.in_(user_categories),
                    Deal.category.is_(None)
                )
            )
        
        query = query.filter(Deal.in_stock == True)
        query = query.filter(DealScore.id != None)
        query = query.filter(DealScore.flip_score >= 70)
        query = query.filter(DealScore.recommended_action == "BUY")
        
        if category:
            query = query.filter(Deal.category == category)
        
        query = query.order_by(DealScore.flip_score.desc()).limit(effective_limit)
        deals = query.all()
        
        result = []
        show_vinted = limits.vinted_scoring
        
        for deal in deals:
            score = session.query(DealScore).filter(DealScore.deal_id == deal.id).first()
            vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal.id).first()
            result.append(_deal_to_frontend_format(deal, score, vinted, show_vinted=show_vinted))
        
        response = {"deals": result, "tier": tier.value}
        
        if limits.max_top_deals is not None and limit > limits.max_top_deals:
            response["upgrade_required"] = True
            response["message"] = f"Upgrade to see more top deals (showing {limits.max_top_deals} of {limit} requested)"
        
        return response


@router.get("/tier-info")
def get_current_tier_info(request: Request):
    """Get current user's subscription tier and limits."""
    _, tier, plan = get_user_info_from_request(request)
    return get_tier_info(plan)


@router.get("/{deal_id:int}/score")
def get_deal_score(request: Request, deal_id: int):
    """Get detailed score for a specific deal."""
    _, tier, _ = get_user_info_from_request(request)
    limits = get_tier_limits(tier)
    show_vinted = limits.vinted_scoring
    
    with get_db_session() as session:
        deal = session.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
        vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        
        response = {
            "deal_id": deal_id,
            "title": deal.title,
            "retail_price": deal.price,
            "score": {
                "flip_score": score.flip_score if score else None,
                "margin_score": score.margin_score if score else None,
                "liquidity_score": score.liquidity_score if score else None,
                "popularity_score": score.popularity_score if score else None,
                "recommended_action": score.recommended_action if score else None,
                "recommended_price": score.recommended_price if score else None,
                "confidence": score.confidence if score else None,
                "explanation": score.explanation if score else None,
                "risks": score.risks if score else [],
            } if score else None,
            "tier": tier.value,
        }
        
        # Vinted stats only for users with vinted_scoring
        if show_vinted and vinted:
            response["vinted"] = {
                "nb_listings": vinted.nb_listings,
                "price_median": vinted.price_median,
                "price_min": vinted.price_min,
                "price_max": vinted.price_max,
                "margin_pct": vinted.margin_pct,
                "margin_euro": vinted.margin_euro,
                "liquidity_score": vinted.liquidity_score,
                "sample_listings": vinted.sample_listings if vinted else [],
            }
        elif not show_vinted:
            response["vinted"] = None
            response["vinted_upgrade_required"] = True
        
        return response


@router.get("/{source}")
def list_deals_by_source(
    request: Request,
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: float = None,
    max_price: float = None,
):
    """Liste les deals d'une source avec filtres optionnels."""
    _, tier, _ = get_user_info_from_request(request)
    allowed_sources = get_allowed_sources(tier)
    
    if source.lower() not in allowed_sources:
        raise HTTPException(
            status_code=403,
            detail=f"Source '{source}' requires premium subscription"
        )
    
    deals = get_deals_by_source(
        source=source,
        limit=limit,
        offset=offset,
        min_price=min_price,
        max_price=max_price,
    )
    return {"source": source, "count": len(deals), "deals": deals, "tier": tier.value}


@router.get("/{source}/{external_id}")
def get_deal_detail(source: str, external_id: str):
    """Récupère un deal spécifique."""
    deal = get_deal(source, external_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal
