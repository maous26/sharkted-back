"""
Sources Router - Administration des sources de collecte.
Endpoints: /v1/sources/*
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.core.source_policy import (
    get_policy,
    get_all_source_metrics,
    get_source_metrics,
    unblock_source,
    SOURCE_POLICIES,
)

# In-memory settings (would be stored in DB in production)
_scraping_settings = {
    "use_rotating_proxy": False,
    "scrape_interval_minutes": 30,
    "max_concurrent_scrapers": 3,
    "min_margin_percent": 15,
    "min_flip_score": 50,
}

# In-memory logs storage (would be stored in DB in production)
_scraping_logs = []

router = APIRouter(prefix="/v1/sources", tags=["sources"])


@router.get("/status")
def get_sources_status():
    """
    État de toutes les sources configurées.
    Retourne un tableau de sources avec métriques, mode actuel, blocages, etc.
    """
    metrics = get_all_source_metrics()
    result = []

    for source, m in metrics.items():
        policy = get_policy(source)
        result.append({
            "id": source,
            "slug": source,
            "name": source.capitalize(),
            "base_url": f"https://{source}.com",
            "is_active": policy.enabled and not m.is_blocked,
            "priority": 1,
            "last_scraped_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error": m.last_error_type if m.last_error_type else None,
            "total_deals_found": m.total_success,
            "plan_required": "free",
            # Additional fields for detailed view
            "enabled": policy.enabled,
            "configured_mode": policy.mode.value,
            "current_mode": m.current_mode.value,
            "allow_proxy": policy.allow_proxy,
            "allow_browser": policy.allow_browser,
            "total_attempts": m.total_attempts,
            "total_success": m.total_success,
            "total_failures": m.total_failures,
            "success_rate_24h": m.success_rate_24h,
            "is_blocked": m.is_blocked,
            "blocked_until": m.blocked_until.isoformat() if m.blocked_until else None,
            "consecutive_failures": m.consecutive_failures,
        })

    return result


@router.get("/{source}/status")
def get_source_status(source: str):
    """État détaillé d'une source spécifique."""
    if source not in SOURCE_POLICIES:
        raise HTTPException(status_code=404, detail=f"Source '{source}' not configured")

    policy = get_policy(source)
    m = get_source_metrics(source)

    return {
        "source": source,
        "policy": {
            "mode": policy.mode.value,
            "enabled": policy.enabled,
            "reason": policy.reason,
            "max_retries": policy.max_retries,
            "base_interval_sec": policy.base_interval_sec,
            "allow_proxy": policy.allow_proxy,
            "allow_browser": policy.allow_browser,
        },
        "metrics": {
            "current_mode": m.current_mode.value,
            "total_attempts": m.total_attempts,
            "total_success": m.total_success,
            "total_failures": m.total_failures,
            "success_rate_24h": m.success_rate_24h,
            "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error_at": m.last_error_at.isoformat() if m.last_error_at else None,
            "last_error_type": m.last_error_type,
            "last_status_code": m.last_status_code,
            "is_blocked": m.is_blocked,
            "blocked_until": m.blocked_until.isoformat() if m.blocked_until else None,
            "consecutive_failures": m.consecutive_failures,
        },
    }


@router.post("/{source}/unblock")
def unblock_source_endpoint(source: str):
    """Débloque manuellement une source."""
    if source not in SOURCE_POLICIES:
        raise HTTPException(status_code=404, detail=f"Source '{source}' not configured")

    was_blocked = unblock_source(source)
    m = get_source_metrics(source)

    return {
        "source": source,
        "was_blocked": was_blocked,
        "current_mode": m.current_mode.value,
        "message": f"Source '{source}' unblocked" if was_blocked else f"Source '{source}' was not blocked",
    }


# =============================================================================
# SETTINGS ENDPOINTS
# =============================================================================

class ScrapingSettingsUpdate(BaseModel):
    use_rotating_proxy: Optional[bool] = None
    scrape_interval_minutes: Optional[int] = None
    max_concurrent_scrapers: Optional[int] = None
    min_margin_percent: Optional[float] = None
    min_flip_score: Optional[int] = None


@router.get("/settings")
def get_settings():
    """Get current scraping settings."""
    return _scraping_settings


@router.patch("/settings")
def update_settings(payload: ScrapingSettingsUpdate):
    """Update scraping settings."""
    if payload.use_rotating_proxy is not None:
        _scraping_settings["use_rotating_proxy"] = payload.use_rotating_proxy
    if payload.scrape_interval_minutes is not None:
        _scraping_settings["scrape_interval_minutes"] = payload.scrape_interval_minutes
    if payload.max_concurrent_scrapers is not None:
        _scraping_settings["max_concurrent_scrapers"] = payload.max_concurrent_scrapers
    if payload.min_margin_percent is not None:
        _scraping_settings["min_margin_percent"] = payload.min_margin_percent
    if payload.min_flip_score is not None:
        _scraping_settings["min_flip_score"] = payload.min_flip_score

    return {**_scraping_settings, "message": "Settings updated successfully"}


# =============================================================================
# LOGS ENDPOINTS
# =============================================================================

@router.get("/logs")
def get_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Get scraping logs with pagination."""
    start = (page - 1) * page_size
    end = start + page_size

    logs = _scraping_logs[start:end]

    return {
        "logs": logs,
        "total": len(_scraping_logs),
        "page": page,
        "page_size": page_size,
        "total_pages": (len(_scraping_logs) + page_size - 1) // page_size if _scraping_logs else 0,
    }


@router.delete("/logs/{log_id}")
def delete_log(log_id: str):
    """Delete a specific log entry."""
    global _scraping_logs
    original_len = len(_scraping_logs)
    _scraping_logs = [log for log in _scraping_logs if log.get("id") != log_id]

    if len(_scraping_logs) == original_len:
        raise HTTPException(status_code=404, detail="Log not found")

    return {"message": "Log deleted", "id": log_id}


@router.delete("/logs")
def delete_old_logs(older_than_days: int = Query(..., ge=1)):
    """Delete logs older than specified days."""
    global _scraping_logs
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    original_len = len(_scraping_logs)

    _scraping_logs = [
        log for log in _scraping_logs
        if datetime.fromisoformat(log.get("timestamp", "2099-01-01")) > cutoff
    ]

    deleted = original_len - len(_scraping_logs)
    return {"message": f"Deleted {deleted} logs older than {older_than_days} days"}


@router.post("/proxies/reload")
def reload_proxies():
    """Reload proxy list (placeholder)."""
    return {"message": "Proxies reloaded", "count": 0}
