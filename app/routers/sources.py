"""
Sources Router - Administration des sources de collecte.
Endpoints: /v1/sources/*
"""
from fastapi import APIRouter, HTTPException

from app.core.source_policy import (
    get_policy,
    get_all_source_metrics,
    get_source_metrics,
    unblock_source,
    SOURCE_POLICIES,
)

router = APIRouter(prefix="/v1/sources", tags=["sources"])


@router.get("/status")
def get_sources_status():
    """
    État de toutes les sources configurées.
    Retourne les métriques, mode actuel, blocages, etc.
    """
    metrics = get_all_source_metrics()
    result = {}

    for source, m in metrics.items():
        policy = get_policy(source)
        result[source] = {
            "source": source,
            "enabled": policy.enabled,
            "configured_mode": policy.mode.value,
            "current_mode": m.current_mode.value,
            "allow_proxy": policy.allow_proxy,
            "allow_browser": policy.allow_browser,
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
        }

    return {"sources": result, "count": len(result)}


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
