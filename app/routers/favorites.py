"""
Favorites Router - Gestion des deals favoris.
Endpoints: /v1/favorites/*
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

from app.services.deal_service import get_db_session
from app.models.deal import Deal
from app.models.deal_score import DealScore
from app.models.vinted_stats import VintedStats
from app.models.user_favorite import UserFavorite
from sqlalchemy import func

router = APIRouter(prefix="/v1/favorites", tags=["favorites"])


class AddFavoriteRequest(BaseModel):
    deal_id: int
    notes: Optional[str] = None


def _deal_to_dict(deal, score=None, vinted=None):
    """Convert deal to dict format."""
    return {
        "id": str(deal.id),
        "product_name": deal.title,
        "brand": deal.brand or deal.seller_name,
        "sale_price": deal.price,
        "original_price": deal.original_price,
        "discount_pct": deal.discount_percent,
        "product_url": deal.url,
        "image_url": deal.image_url,
        "source_name": deal.source,
        "in_stock": deal.in_stock,
        "detected_at": deal.first_seen_at.isoformat() if deal.first_seen_at else None,
        "score": {
            "flip_score": float(score.flip_score) if score.flip_score else 0,
            "margin_score": float(score.margin_score) if score.margin_score else 0,
            "liquidity_score": float(score.liquidity_score) if score.liquidity_score else 0,
            "popularity_score": float(score.popularity_score) if score.popularity_score else 0,
            "recommended_action": score.recommended_action.lower() if score.recommended_action else None,
            "explanation_short": score.explanation_short,
            "risks": score.risks or [],
            "estimated_sell_days": score.estimated_sell_days,
        } if score else None,
        "vinted_stats": {
            "nb_listings": vinted.nb_listings,
            "price_median": float(vinted.price_median) if vinted.price_median else None,
            "margin_euro": float(vinted.margin_euro) if vinted.margin_euro else None,
            "margin_pct": float(vinted.margin_pct) if vinted.margin_pct else None,
            "liquidity_score": float(vinted.liquidity_score) if vinted.liquidity_score else None,
        } if vinted else None,
    }


@router.get("")
def list_favorites(
    user_id: int = Query(..., description="User ID"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """Liste les favoris d'un utilisateur."""
    with get_db_session() as session:
        # Count total
        total = session.query(func.count(UserFavorite.id)).filter(
            UserFavorite.user_id == user_id
        ).scalar() or 0
        
        # Get favorites with pagination
        favorites = session.query(UserFavorite).filter(
            UserFavorite.user_id == user_id
        ).order_by(UserFavorite.created_at.desc()).offset(
            (page - 1) * per_page
        ).limit(per_page).all()
        
        result = []
        for fav in favorites:
            deal = session.query(Deal).filter(Deal.id == fav.deal_id).first()
            score = session.query(DealScore).filter(DealScore.deal_id == fav.deal_id).first()
            vinted = session.query(VintedStats).filter(VintedStats.deal_id == fav.deal_id).first()
            
            result.append({
                "id": fav.id,
                "deal_id": fav.deal_id,
                "notes": fav.notes,
                "created_at": fav.created_at.isoformat() if fav.created_at else None,
                "deal": _deal_to_dict(deal, score, vinted) if deal else None,
            })
        
        return {
            "favorites": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if total > 0 else 0,
        }


@router.post("")
def add_favorite(
    request: AddFavoriteRequest,
    user_id: int = Query(..., description="User ID"),
):
    """Ajoute un deal aux favoris."""
    with get_db_session() as session:
        # Vérifier si le deal existe
        deal = session.query(Deal).filter(Deal.id == request.deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        
        # Vérifier si déjà en favori
        existing = session.query(UserFavorite).filter(
            UserFavorite.user_id == user_id,
            UserFavorite.deal_id == request.deal_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Deal already in favorites")
        
        # Créer le favori
        favorite = UserFavorite(
            user_id=user_id,
            deal_id=request.deal_id,
            notes=request.notes
        )
        session.add(favorite)
        session.commit()
        session.refresh(favorite)
        
        return {
            "id": favorite.id,
            "deal_id": favorite.deal_id,
            "notes": favorite.notes,
            "created_at": favorite.created_at.isoformat() if favorite.created_at else None,
            "message": "Deal added to favorites"
        }


@router.delete("/{deal_id}")
def remove_favorite(
    deal_id: int,
    user_id: int = Query(..., description="User ID"),
):
    """Retire un deal des favoris."""
    with get_db_session() as session:
        favorite = session.query(UserFavorite).filter(
            UserFavorite.user_id == user_id,
            UserFavorite.deal_id == deal_id
        ).first()
        
        if not favorite:
            raise HTTPException(status_code=404, detail="Favorite not found")
        
        session.delete(favorite)
        session.commit()
        
        return {"message": "Deal removed from favorites", "deal_id": deal_id}


@router.get("/check/{deal_id}")
def check_favorite(
    deal_id: int,
    user_id: int = Query(..., description="User ID"),
):
    """Vérifie si un deal est en favori."""
    with get_db_session() as session:
        favorite = session.query(UserFavorite).filter(
            UserFavorite.user_id == user_id,
            UserFavorite.deal_id == deal_id
        ).first()
        
        return {
            "is_favorite": favorite is not None,
            "favorite_id": favorite.id if favorite else None
        }


@router.get("/ids")
def get_favorite_ids(
    user_id: int = Query(..., description="User ID"),
):
    """Retourne la liste des IDs des deals favoris (pour vérification rapide côté client)."""
    with get_db_session() as session:
        favorites = session.query(UserFavorite.deal_id).filter(
            UserFavorite.user_id == user_id
        ).all()
        
        return {
            "deal_ids": [f.deal_id for f in favorites]
        }
