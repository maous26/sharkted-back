"""
Deals Router - Consultation des deals persistés.
Endpoints: /v1/deals/*
"""
from fastapi import APIRouter, HTTPException, Query, Request
from datetime import datetime, timedelta
from typing import Optional, List
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
from app.models.subscription import get_user_tier, get_tier_limits, get_tier_sources, SubscriptionTier
from app.core.config import JWT_SECRET, JWT_ALGO
from sqlalchemy import func, or_, and_
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/v1/deals", tags=["deals"])


def _get_request_user(request: Request) -> Optional[User]:
    """
    Extract user from token manually for segmentation logic.
    Returns None for anonymous.
    """
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        return None

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        if not email:
            return None
    except JWTError:
        return None

    # We need a new session here usually, but be careful with connections
    with get_db_session() as session:
        # Eager load preferences for categories
        # Note: detached object might be risky if we access relationships later outside session
        # But here we just need plan and preferences which are basic columns/JSON
        user = session.query(User).filter(User.email == email).first()
        if user:
            session.expunge(user) # Detach to use outside
            return user
        return None


def _deal_to_frontend_format(deal, score=None, vinted=None):
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
        # Vinted stats
        "vinted_stats": {
            "nb_listings": vinted.nb_listings,
            "price_min": vinted.price_min,
            "price_max": vinted.price_max,
            "price_median": vinted.price_median,
            "margin_euro": vinted.margin_euro,
            "margin_pct": vinted.margin_pct,
            "liquidity_score": vinted.liquidity_score,
        } if vinted else None,
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
    Liste tous les deals avec segmentation par offre (Freemium/Shark/Whale).
    """
    # 1. Identify User and Tier
    user = _get_request_user(request)
    if user and user.subscription_status == "active":
        plan = user.plan
    else:
        plan = "free"  # Default to free if no auth or inactive sub
    
    tier = get_user_tier(plan)
    limits = get_tier_limits(tier)
    allowed_sources = get_tier_sources(tier)
    
    # 2. Setup Query
    with get_db_session() as session:
        # Base query
        query = session.query(Deal).outerjoin(
            DealScore, Deal.id == DealScore.deal_id
        ).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        )
        
        # 3. Apply Segmentation Filters
        
        # Source Restrictions (Shark/Freemium vs Whale)
        if allowed_sources:
             query = query.filter(Deal.source.in_(allowed_sources))

        # Category Restrictions (Freemium -> Sneakers only)
        # Note: If user passes ?category=other, it will return empty if restricted
        if limits.get("allowed_categories"):
            tier_cats = limits["allowed_categories"]
            # If request asks for a specific category, check if it's allowed
            if category:
                if category not in tier_cats:
                    # User asked for forbidden category -> Return empty or filter to 0
                    query = query.filter(1 == 0) # Force empty
                else:
                    query = query.filter(Deal.category == category)
            else:
                # Force limit to allowed categories
                query = query.filter(Deal.category.in_(tier_cats))
        else:
            # User Preference Categories (for Paid users who have preferences)
            if user and user.preferences:
                pref_cats = user.preferences.get("categories", [])
                if pref_cats and len(pref_cats) > 0 and not category:
                    # Apply preferences only if no explicit filter
                    query = query.filter(
                        or_(
                            Deal.category.in_(pref_cats),
                            Deal.category.is_(None)
                        )
                    )
            # Apply requested filter if any
            if category:
                query = query.filter(Deal.category == category)

        # 4. Standard Filters
        if source:
            query = query.filter(Deal.source == source)
        
        if brand:
            query = query.filter(
                or_(
                    Deal.brand.ilike(f"%{brand}%"),
                    Deal.seller_name.ilike(f"%{brand}%"),
                    Deal.title.ilike(f"%{brand}%")
                )
            )
            
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
            
        # Standard: In Stock and Scored
        query = query.filter(Deal.in_stock == True)
        query = query.filter(DealScore.id != None)
        query = query.filter(DealScore.flip_score >= 60)
        
        # 5. Sorting
        if sort_by == "flip_score":
            sort_col = DealScore.flip_score
        elif sort_by == "margin_pct":
            sort_col = VintedStats.margin_pct
        elif sort_by == "price" or sort_by == "sale_price":
            sort_col = Deal.price
        else:
            sort_col = Deal.first_seen_at
            
        # 6. Pagination & Freemium Teaser Logic
        
        if tier == SubscriptionTier.FREEMIUM:
            # FREEMIUM SPECIAL LOGIC: 5 Deals max. 1 Top (>70), 4 Others.
            # We ignore page/per_page from request and enforce limit
            
            # Fetch Top Deal (Teaser) - Score > 70
            top_deal_q = query.filter(DealScore.flip_score > 70).order_by(DealScore.flip_score.desc()).limit(1)
            top_deals = top_deal_q.all()
            
            # Fetch Regular Deals - Score <= 70 (to avoid showing more top deals)
            # Limit 4 (or 5 minus count of top deals)
            needed = 5 - len(top_deals)
            regular_q = query.filter(DealScore.flip_score <= 70)
            
            if sort_order == "asc":
                regular_q = regular_q.order_by(sort_col.asc().nullslast())
            else:
                 regular_q = regular_q.order_by(sort_col.desc().nullsfirst())
                 
            regular_deals = regular_q.limit(needed).all()
            
            deals = top_deals + regular_deals
            total = len(deals) # Artificial total
            page = 1
            per_page = 5
            
        else:
            # PAID TIERS (Shark/Whale) - Standard Pagination
            
            if sort_order == "asc":
                query = query.order_by(sort_col.asc().nullslast())
            else:
                query = query.order_by(sort_col.desc().nullsfirst())
            
            total = query.count()
            offset = (page - 1) * per_page
            deals = query.offset(offset).limit(per_page).all()

        # Build Response
        result = []
        for deal in deals:
            score = session.query(DealScore).filter(DealScore.deal_id == deal.id).first()
            vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal.id).first()
            result.append(_deal_to_frontend_format(deal, score, vinted))
        
        return {
            "deals": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 1,
            "role": tier, # Return role for frontend debug
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
        # Total deals
        total = session.query(func.count(Deal.id)).scalar() or 0

        # Active deals (in stock)
        # Active deals (in stock AND qualified > 60 score)
        active = session.query(func.count(Deal.id)).join(DealScore, Deal.id == DealScore.deal_id).filter(
            Deal.in_stock == True,
            DealScore.flip_score >= 60
        ).scalar() or 0

        # Deals added today
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = session.query(func.count(Deal.id)).filter(Deal.first_seen_at >= today_start).scalar() or 0

        # Average price
        avg_price = session.query(func.avg(Deal.price)).filter(Deal.in_stock == True).scalar() or 0

        # By source
        by_source = dict(session.query(Deal.source, func.count(Deal.id)).group_by(Deal.source).all())
        
        # Scoring stats
        scored_count = session.query(func.count(DealScore.id)).scalar() or 0
        avg_score = session.query(func.avg(DealScore.flip_score)).scalar() or 0
        buy_count = session.query(func.count(DealScore.id)).filter(DealScore.recommended_action == "BUY").scalar() or 0
        
        # Margin stats
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
        # Get brand from seller_name as fallback
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
        # Use source as proxy for category
        results = session.query(
            Deal.source,
            func.count(Deal.id).label("count"),
        ).filter(
            Deal.in_stock == True
        ).group_by(Deal.source).all()

        return [
            {
                "category": source.capitalize(),
                "deal_count": count,
            }
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

        data = [
            {"date": str(date), "value": count}
            for date, count in results
        ]

        return {"data": data, "days": days}


@router.get("/stats/score-distribution")
def deals_stats_score_distribution():
    """Distribution des scores des deals."""
    with get_db_session() as session:
        ranges = [
            (0, 20, "0-20"),
            (20, 40, "20-40"),
            (40, 60, "40-60"),
            (60, 80, "60-80"),
            (80, 100, "80-100"),
        ]

        distribution = []
        total = 0
        for min_score, max_score, label in ranges:
            if max_score < 100:
                count = session.query(func.count(DealScore.id)).filter(
                    DealScore.flip_score >= min_score,
                    DealScore.flip_score < max_score
                ).scalar() or 0
            else:
                count = session.query(func.count(DealScore.id)).filter(
                    DealScore.flip_score >= min_score,
                    DealScore.flip_score <= 100
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
    """Get top recommended deals by FlipScore."""
    # Get user categories (extracted before main DB session)
    user_categories = get_user_categories_from_request(request)
    
    with get_db_session() as session:
        query = session.query(Deal).outerjoin(
            DealScore, Deal.id == DealScore.deal_id
        ).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        )
        
        # Apply user category filter from preferences
        # Include uncategorized deals (NULL) as well
        if user_categories:
            query = query.filter(
                or_(
                    Deal.category.in_(user_categories),
                    Deal.category.is_(None)
                )
            )
        
        # Only in stock and scored
        query = query.filter(Deal.in_stock == True)
        query = query.filter(DealScore.id != None)
        query = query.filter(DealScore.flip_score >= 70)  # Higher threshold for "top"
        query = query.filter(DealScore.recommended_action == "BUY")
        
        if category:
            query = query.filter(Deal.category == category)
        
        # Order by flip_score descending
        query = query.order_by(DealScore.flip_score.desc()).limit(limit)
        
        deals = query.all()
        
        result = []
        for deal in deals:
            score = session.query(DealScore).filter(DealScore.deal_id == deal.id).first()
            vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal.id).first()
            result.append(_deal_to_frontend_format(deal, score, vinted))
        
        return {"deals": result}


@router.get("/{deal_id:int}/score")
def get_deal_score(deal_id: int):
    """Get detailed score for a specific deal."""
    with get_db_session() as session:
        deal = session.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
        vinted = session.query(VintedStats).filter(VintedStats.deal_id == deal_id).first()
        
        return {
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
            "vinted": {
                "nb_listings": vinted.nb_listings if vinted else None,
                "price_median": vinted.price_median if vinted else None,
                "price_min": vinted.price_min if vinted else None,
                "price_max": vinted.price_max if vinted else None,
                "margin_pct": vinted.margin_pct if vinted else None,
                "margin_euro": vinted.margin_euro if vinted else None,
                "liquidity_score": vinted.liquidity_score if vinted else None,
                "sample_listings": vinted.sample_listings if vinted else [],
            } if vinted else None,
        }


@router.get("/{source}")
def list_deals_by_source(
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: float = None,
    max_price: float = None,
):
    """Liste les deals d une source avec filtres optionnels."""
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
