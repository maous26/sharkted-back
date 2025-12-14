"""
Alerts Router - User notifications and alerts.
Endpoints: /v1/alerts/*
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from jose import jwt, JWTError

from app.db.deps import get_db
from app.models.user import User, Alert
from app.core.config import JWT_SECRET, JWT_ALGO

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Extract and validate current user from token."""
    # Try cookie first, then Authorization header
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


@router.get("")
def list_alerts(
    page: int = 1,
    per_page: int = 20,
    unread_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List alerts for current user with pagination."""
    query = db.query(Alert).filter(Alert.user_id == current_user.id)

    if unread_only:
        query = query.filter(Alert.is_read == False)

    total = query.count()
    alerts = (
        query
        .order_by(desc(Alert.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "items": [
            {
                "id": str(a.id),
                "type": a.type,
                "title": a.title,
                "message": a.message,
                "deal_id": a.deal_id,
                "is_read": a.is_read,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page > 0 else 0,
    }


@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get count of unread alerts."""
    count = db.query(Alert).filter(
        Alert.user_id == current_user.id,
        Alert.is_read == False
    ).count()
    return {"count": count}


@router.get("/stats")
def get_alert_stats(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get alert statistics for the user."""
    since = datetime.utcnow() - timedelta(days=days)

    total = db.query(Alert).filter(
        Alert.user_id == current_user.id,
        Alert.created_at >= since
    ).count()

    unread = db.query(Alert).filter(
        Alert.user_id == current_user.id,
        Alert.is_read == False,
        Alert.created_at >= since
    ).count()

    # Count by type
    by_type = (
        db.query(Alert.type, func.count(Alert.id))
        .filter(Alert.user_id == current_user.id, Alert.created_at >= since)
        .group_by(Alert.type)
        .all()
    )

    return {
        "days": days,
        "total": total,
        "unread": unread,
        "read": total - unread,
        "by_type": {t: c for t, c in by_type},
    }


@router.patch("/{alert_id}/read")
def mark_alert_read(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a single alert as read."""
    alert = db.query(Alert).filter(
        Alert.id == alert_id,
        Alert.user_id == current_user.id
    ).first()

    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.is_read = True
    db.commit()

    return {"status": "ok"}


@router.patch("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all alerts as read for current user."""
    db.query(Alert).filter(
        Alert.user_id == current_user.id,
        Alert.is_read == False
    ).update({"is_read": True})
    db.commit()

    return {"status": "ok"}
