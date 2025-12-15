"""
Router Outcomes - API pour tracker les achats/ventes utilisateurs
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from jose import jwt, JWTError

from app.db.deps import get_db
from app.models.outcome import Outcome
from app.models.deal import Deal
from app.models.user import User
from app.core.config import JWT_SECRET, JWT_ALGO

router = APIRouter(prefix="/v1/outcomes", tags=["outcomes"])


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Extract and validate current user from token."""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# Schemas
class OutcomeCreate(BaseModel):
    deal_id: Optional[int] = None
    action: str
    buy_price: Optional[float] = None
    buy_date: Optional[datetime] = None
    buy_size: Optional[str] = None
    buy_platform: Optional[str] = None
    context_snapshot: Optional[dict] = None
    notes: Optional[str] = None


class OutcomeSellUpdate(BaseModel):
    sell_price: float
    sell_date: Optional[datetime] = None
    sell_platform: Optional[str] = None
    was_good_deal: Optional[bool] = None
    difficulty_rating: Optional[int] = None
    notes: Optional[str] = None


class OutcomeResponse(BaseModel):
    id: int
    deal_id: Optional[int]
    action: str
    buy_price: Optional[float]
    buy_date: Optional[datetime]
    buy_size: Optional[str]
    buy_platform: Optional[str]
    sold: bool
    sell_price: Optional[float]
    sell_date: Optional[datetime]
    sell_platform: Optional[str]
    actual_margin_euro: Optional[float]
    actual_margin_pct: Optional[float]
    days_to_sell: Optional[int]
    was_good_deal: Optional[bool]
    difficulty_rating: Optional[int]
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=OutcomeResponse)
def create_outcome(
    payload: OutcomeCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    
    if payload.deal_id:
        deal = db.query(Deal).filter(Deal.id == payload.deal_id).first()
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
    
    outcome = Outcome(
        user_id=current_user.id,
        deal_id=payload.deal_id,
        action=payload.action,
        buy_price=payload.buy_price,
        buy_date=payload.buy_date or (datetime.utcnow() if payload.action == "bought" else None),
        buy_size=payload.buy_size,
        buy_platform=payload.buy_platform,
        context_snapshot=payload.context_snapshot,
        notes=payload.notes,
    )
    
    db.add(outcome)
    db.commit()
    db.refresh(outcome)
    return outcome


@router.get("", response_model=List[OutcomeResponse])
def list_outcomes(
    request: Request,
    action: Optional[str] = Query(None),
    sold: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    current_user = get_current_user(request, db)
    query = db.query(Outcome).filter(Outcome.user_id == current_user.id)
    
    if action:
        query = query.filter(Outcome.action == action)
    if sold is not None:
        query = query.filter(Outcome.sold == sold)
    
    return query.order_by(Outcome.created_at.desc()).offset(offset).limit(limit).all()


@router.get("/stats")
def get_outcome_stats(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    user_id = current_user.id
    
    bought = db.query(func.count(Outcome.id)).filter(
        Outcome.user_id == user_id, Outcome.action == "bought"
    ).scalar() or 0
    
    sold_count = db.query(func.count(Outcome.id)).filter(
        Outcome.user_id == user_id, Outcome.sold == True
    ).scalar() or 0
    
    avg_margin_euro = db.query(func.avg(Outcome.actual_margin_euro)).filter(
        Outcome.user_id == user_id, Outcome.sold == True
    ).scalar()
    
    avg_margin_pct = db.query(func.avg(Outcome.actual_margin_pct)).filter(
        Outcome.user_id == user_id, Outcome.sold == True
    ).scalar()
    
    avg_days = db.query(func.avg(Outcome.days_to_sell)).filter(
        Outcome.user_id == user_id, Outcome.sold == True
    ).scalar()
    
    total_profit = db.query(func.sum(Outcome.actual_margin_euro)).filter(
        Outcome.user_id == user_id, Outcome.sold == True
    ).scalar() or 0
    
    return {
        "total_bought": bought,
        "total_sold": sold_count,
        "pending_sale": bought - sold_count,
        "sell_rate_pct": round((sold_count / bought * 100) if bought > 0 else 0, 1),
        "avg_margin_euro": round(avg_margin_euro, 2) if avg_margin_euro else None,
        "avg_margin_pct": round(avg_margin_pct, 1) if avg_margin_pct else None,
        "avg_days_to_sell": round(avg_days, 1) if avg_days else None,
        "total_profit": round(total_profit, 2),
    }


@router.patch("/{outcome_id}/sell", response_model=OutcomeResponse)
def mark_as_sold(outcome_id: int, payload: OutcomeSellUpdate, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    
    outcome = db.query(Outcome).filter(
        Outcome.id == outcome_id, Outcome.user_id == current_user.id
    ).first()
    
    if not outcome:
        raise HTTPException(status_code=404, detail="Outcome not found")
    if outcome.action != "bought":
        raise HTTPException(status_code=400, detail="Can only mark bought items as sold")
    
    outcome.sold = True
    outcome.sell_price = payload.sell_price
    outcome.sell_date = payload.sell_date or datetime.utcnow()
    outcome.sell_platform = payload.sell_platform
    outcome.was_good_deal = payload.was_good_deal
    outcome.difficulty_rating = payload.difficulty_rating
    if payload.notes:
        outcome.notes = payload.notes
    
    if outcome.buy_price and outcome.sell_price:
        outcome.actual_margin_euro = outcome.sell_price - outcome.buy_price
        outcome.actual_margin_pct = (outcome.actual_margin_euro / outcome.buy_price) * 100
    
    if outcome.buy_date and outcome.sell_date:
        outcome.days_to_sell = (outcome.sell_date - outcome.buy_date).days
    
    outcome.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(outcome)
    return outcome


@router.delete("/{outcome_id}")
def delete_outcome(outcome_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = get_current_user(request, db)
    
    outcome = db.query(Outcome).filter(
        Outcome.id == outcome_id, Outcome.user_id == current_user.id
    ).first()
    
    if not outcome:
        raise HTTPException(status_code=404, detail="Outcome not found")
    
    db.delete(outcome)
    db.commit()
    return {"status": "deleted", "id": outcome_id}
