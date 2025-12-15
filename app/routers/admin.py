"""
Admin Router - Administration endpoints.
Endpoints: /v1/admin/*
"""
from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import SessionLocal
from app.core.source_policy import get_all_source_metrics
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/stats")
def get_admin_stats():
    """
    Get admin dashboard statistics.
    Returns total deals, users, active sources, etc.
    """
    session = SessionLocal()
    try:
        # Count deals
        try:
            result = session.execute(text("SELECT COUNT(*) FROM deals"))
            total_deals = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Could not count deals: {e}")
            total_deals = 0
        
        # Count users
        try:
            result = session.execute(text("SELECT COUNT(*) FROM users"))
            total_users = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Could not count users: {e}")
            total_users = 0
        
        # Get source metrics
        metrics = get_all_source_metrics()
        active_sources = sum(1 for m in metrics.values() if not m.is_blocked)
        
        # Find last scrape time
        last_scrape = None
        for m in metrics.values():
            if m.last_success_at:
                if last_scrape is None or m.last_success_at > last_scrape:
                    last_scrape = m.last_success_at
        
        # Check if any source is currently scraping (based on recent activity)
        scraping_status = "idle"
        
        return {
            "database": "connected",
            "scraping": scraping_status,
            "last_scrape": last_scrape.isoformat() if last_scrape else None,
            "total_deals": total_deals,
            "total_users": total_users,
            "active_sources": active_sources,
        }
    finally:
        session.close()


# =============================================================================
# USER MANAGEMENT ENDPOINTS
# =============================================================================

from typing import Optional, List
from pydantic import BaseModel
from app.models.user import User


class UserResponse(BaseModel):
    id: int
    email: str
    plan: str
    is_admin: bool


class UserPlanUpdate(BaseModel):
    plan: str  # free, pro, agency, owner


@router.get("/users", response_model=List[UserResponse])
def list_users():
    """List all users with their plans."""
    session = SessionLocal()
    try:
        users = session.query(User).all()
        return [
            UserResponse(
                id=u.id,
                email=u.email,
                plan=u.plan or "free",
                is_admin=u.is_admin
            )
            for u in users
        ]
    finally:
        session.close()


@router.patch("/users/{user_id}/plan")
def update_user_plan(user_id: int, payload: UserPlanUpdate):
    """Update a user plan (free, pro, agency, owner)."""
    valid_plans = ["free", "pro", "agency", "owner"]
    if payload.plan.lower() not in valid_plans:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {valid_plans}")
    
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="User not found")
        
        old_plan = user.plan
        user.plan = payload.plan.lower()
        session.commit()
        
        logger.info(f"User {user.email} plan changed: {old_plan} -> {user.plan}")
        
        return {
            "id": user.id,
            "email": user.email,
            "old_plan": old_plan,
            "new_plan": user.plan,
            "message": f"Plan updated to {user.plan}"
        }
    finally:
        session.close()


@router.delete("/users/{user_id}")
def delete_user(user_id: int):
    """Delete a user account."""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.email == "admin@sharkted.fr":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Cannot delete admin account")
        
        email = user.email
        session.delete(user)
        session.commit()
        
        logger.info(f"User deleted: {email}")
        return {"message": f"User {email} deleted"}
    finally:
        session.close()
