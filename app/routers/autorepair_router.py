"""
Auto-Repair Router - Endpoints pour diagnostiquer et réparer les scrapers.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from loguru import logger

from app.services.scraping_autorepair import (
    diagnose_and_repair,
    check_all_sources_health,
    get_recent_failures,
)
from app.services.scraping_service import SOURCE_LISTING_URLS


router = APIRouter(prefix="/v1/admin/scraping", tags=["admin"])


@router.get("/health")
async def check_sources_health():
    """
    Vérifie la santé de toutes les sources actives.
    Retourne quelles sources fonctionnent et lesquelles ont besoin de réparation.
    """
    try:
        results = check_all_sources_health()

        # Summary
        ok = [s for s, r in results.items() if r.get("status") == "ok"]
        errors = [s for s, r in results.items() if r.get("needs_repair")]

        return {
            "status": "ok" if not errors else "degraded",
            "healthy_sources": ok,
            "sources_needing_repair": errors,
            "details": results
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/failures")
async def get_failures(hours: int = Query(24, ge=1, le=168)):
    """
    Récupère les échecs de scraping récents.
    """
    try:
        failures = get_recent_failures(hours)
        return {
            "hours": hours,
            "total_failures": len(failures),
            "failures": failures
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/diagnose/{source}")
async def diagnose_source(
    source: str,
    url: Optional[str] = None,
    autofix: bool = Query(False, description="Appliquer automatiquement le fix si confiance > 70%")
):
    """
    Diagnostique un scraper cassé avec l'IA.

    - source: Nom de la source (courir, asos, printemps, etc.)
    - url: URL spécifique à tester (sinon utilise la première URL de listing)
    - autofix: Si True et confiance > 70%, applique le fix automatiquement
    """
    # Get listing URL
    listing_url = url
    if not listing_url:
        urls = SOURCE_LISTING_URLS.get(source, [])
        if not urls:
            raise HTTPException(status_code=404, detail=f"No URLs configured for {source}")
        listing_url = urls[0]

    logger.info(f"Diagnosing {source} with URL: {listing_url}")

    try:
        result = await diagnose_and_repair(
            source=source,
            listing_url=listing_url,
            autofix=autofix
        )
        return result
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/repair-all")
async def repair_all_broken(
    autofix: bool = Query(False, description="Appliquer les fixes automatiquement")
):
    """
    Diagnostique et répare toutes les sources cassées.
    """
    # First check health
    health = check_all_sources_health()

    broken = [s for s, r in health.items() if r.get("needs_repair")]

    if not broken:
        return {"status": "all_healthy", "repaired": []}

    results = []
    for source in broken:
        try:
            urls = SOURCE_LISTING_URLS.get(source, [])
            if urls:
                result = await diagnose_and_repair(
                    source=source,
                    listing_url=urls[0],
                    autofix=autofix
                )
                results.append(result)
        except Exception as e:
            results.append({
                "source": source,
                "status": "error",
                "error": str(e)
            })

    return {
        "broken_sources": broken,
        "repair_results": results,
        "autofix_enabled": autofix
    }
