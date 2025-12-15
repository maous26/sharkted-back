"""
Sources Router - Administration des sources de collecte.
Endpoints: /v1/sources/*
"""
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import os

from rq import Queue
import redis

from app.core.source_policy import (
    get_policy,
    get_all_source_metrics,
    get_source_metrics,
    unblock_source,
    SOURCE_POLICIES,
)
from app.services.scraping_service import (
    get_scraping_logs,
    delete_scraping_log,
    delete_old_scraping_logs,
    get_enabled_sources,
)
from app.services.proxy_service import get_proxy_pool
from app.jobs_scraping import scrape_source, scrape_all_sources

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_conn = redis.from_url(REDIS_URL)
queue_default = Queue("default", connection=redis_conn)

# In-memory settings (would be stored in DB in production)
_scraping_settings = {
    "use_rotating_proxy": False,
    "scrape_interval_minutes": 30,
    "max_concurrent_scrapers": 3,
    "min_margin_percent": 15,
    "min_flip_score": 50,
}

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
# SCRAPING ENDPOINT
# =============================================================================

class ScrapeRequest(BaseModel):
    sources: Optional[List[str]] = None
    send_alerts: bool = True
    max_products: int = 30


@router.post("/scrape")
def run_scraping(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """
    Lance le scraping des sources.
    
    Args:
        sources: Liste des sources à scraper (None = toutes les activées)
        send_alerts: Envoyer des alertes pour les nouveaux deals
        max_products: Nombre max de produits par source
    
    Returns:
        Job ID et status
    """
    # Déterminer les sources
    if request.sources:
        sources_to_scrape = [s for s in request.sources if s in SOURCE_POLICIES]
        if not sources_to_scrape:
            raise HTTPException(status_code=400, detail="No valid sources provided")
    else:
        sources_to_scrape = get_enabled_sources()
    
    if not sources_to_scrape:
        raise HTTPException(status_code=400, detail="No enabled sources to scrape")
    
    # Enqueue le job
    job = queue_default.enqueue(
        scrape_all_sources,
        sources_to_scrape,
        request.max_products,
        job_timeout=1800,  # 30 minutes max
        result_ttl=3600,
    )
    
    return {
        "status": "enqueued",
        "job_id": job.id,
        "sources": sources_to_scrape,
        "max_products_per_source": request.max_products,
        "message": f"Scraping started for {len(sources_to_scrape)} source(s)",
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
    return get_scraping_logs(page, page_size)


@router.delete("/logs/{log_id}")
def delete_log(log_id: str):
    """Delete a specific log entry."""
    if not delete_scraping_log(log_id):
        raise HTTPException(status_code=404, detail="Log not found")
    return {"message": "Log deleted", "id": log_id}


@router.delete("/logs")
def delete_old_logs(older_than_days: int = Query(..., ge=1)):
    """Delete logs older than specified days."""
    deleted = delete_old_scraping_logs(older_than_days)
    return {"message": f"Deleted {deleted} logs older than {older_than_days} days"}


# =============================================================================
# PROXY ENDPOINTS
# =============================================================================

@router.post("/proxies/reload")
def reload_proxies():
    """Reload proxy pool."""
    pool = get_proxy_pool()
    pool.initialize()
    stats = pool.get_stats()
    return {
        "message": "Proxies reloaded",
        "stats": stats,
    }


@router.get("/proxies/stats")
def get_proxy_stats():
    """Get proxy pool statistics."""
    pool = get_proxy_pool()
    return pool.get_stats()


# =============================================================================
# VINTED STATS RESCRAPING
# =============================================================================

@router.post("/rescrape-vinted-stats")
def rescrape_vinted_stats(
    limit: int = Query(50, ge=1, le=200),
    force: bool = Query(False, description="Force rescrape even if stats exist"),
):
    """
    Lance le rescoring des deals avec les stats Vinted.
    
    Args:
        limit: Nombre de deals à traiter
        force: Forcer le rescrape même si les stats existent déjà
    
    Returns:
        Job ID et status
    """
    from app.jobs_scoring import rescore_deals_batch
    
    job = queue_default.enqueue(
        rescore_deals_batch,
        limit,
        force,
        job_timeout=1800,  # 30 minutes max
        result_ttl=3600,
    )
    
    return {
        "status": "enqueued",
        "job_id": job.id,
        "limit": limit,
        "force": force,
        "message": f"Rescraping Vinted stats for up to {limit} deals",
    }
