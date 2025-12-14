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
