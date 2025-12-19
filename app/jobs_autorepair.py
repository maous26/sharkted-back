"""
Jobs Auto-Repair - Surveillance et réparation automatique des scrapers.

Ce job tourne toutes les heures et:
1. Vérifie la santé de toutes les sources
2. Diagnostique les sources cassées avec l'IA
3. Applique automatiquement les fixes si confiance > 80%
4. Notifie les erreurs critiques
"""

import os
from datetime import datetime
from typing import Dict, Any, List

from app.core.logging import get_logger, set_trace_id
from app.scheduler import is_quiet_hours

logger = get_logger(__name__)

# Seuil de confiance pour appliquer automatiquement un fix
AUTOFIX_CONFIDENCE_THRESHOLD = 0.80


def check_sources_health() -> Dict[str, Any]:
    """
    Vérifie la santé de toutes les sources actives.
    """
    from app.core.source_policy import SOURCE_POLICIES
    from app.services.scraping_service import SOURCE_LISTING_URLS, extract_product_urls
    from app.services.scraping_autorepair import fetch_page_with_proxy

    results = {}

    for source, policy in SOURCE_POLICIES.items():
        if not policy.enabled:
            results[source] = {"status": "disabled"}
            continue

        listing_urls = SOURCE_LISTING_URLS.get(source, [])
        if not listing_urls:
            results[source] = {"status": "no_urls"}
            continue

        try:
            # Test first URL
            html, status = fetch_page_with_proxy(listing_urls[0])

            if status >= 400 or status == 0:
                results[source] = {
                    "status": "error",
                    "http_status": status,
                    "needs_repair": True
                }
            else:
                # Check if we can extract products
                urls = extract_product_urls(html, source)
                results[source] = {
                    "status": "ok" if urls else "no_products",
                    "http_status": status,
                    "products_found": len(urls),
                    "needs_repair": len(urls) == 0
                }
        except Exception as e:
            results[source] = {
                "status": "error",
                "error": str(e)[:100],
                "needs_repair": True
            }

    return results


def diagnose_source(source: str, listing_url: str) -> Dict[str, Any]:
    """
    Diagnostique une source avec l'IA.
    """
    from app.services.scraping_autorepair import (
        fetch_page_with_proxy,
        get_collector_code,
        analyze_with_ai,
        apply_fix,
    )

    # 1. Fetch page
    html, status = fetch_page_with_proxy(listing_url)
    if status == 0:
        return {"source": source, "status": "fetch_error", "error": html}

    # 2. Get collector code
    collector_code = get_collector_code(source)
    if not collector_code:
        return {"source": source, "status": "no_collector"}

    # 3. Analyze with AI
    error_msg = f"HTTP {status}" if status >= 400 else "No products extracted"
    analysis = analyze_with_ai(
        source=source,
        error_message=error_msg,
        html_sample=html,
        collector_code=collector_code,
        listing_url=listing_url
    )

    if "error" in analysis:
        return {"source": source, "status": "analysis_error", "error": analysis["error"]}

    # 4. Auto-apply fix if confidence is high enough
    fix_applied = False
    confidence = analysis.get("confidence", 0)

    if analysis.get("fixed_code") and confidence >= AUTOFIX_CONFIDENCE_THRESHOLD:
        logger.info(f"Auto-applying fix for {source} (confidence: {confidence:.0%})")
        fix_applied = apply_fix(source, analysis["fixed_code"])
        if fix_applied:
            logger.info(f"✅ Fix applied successfully for {source}")
        else:
            logger.warning(f"❌ Failed to apply fix for {source}")

    return {
        "source": source,
        "status": "diagnosed",
        "http_status": status,
        "diagnosis": analysis.get("diagnosis"),
        "changes_detected": analysis.get("changes_detected", []),
        "confidence": confidence,
        "fix_applied": fix_applied,
        "autofix_threshold": AUTOFIX_CONFIDENCE_THRESHOLD,
    }


def scheduled_autorepair() -> Dict[str, Any]:
    """
    Job planifié d'auto-repair - tourne toutes les heures.
    """
    trace_id = set_trace_id()
    start_time = datetime.utcnow()

    # Skip pendant les heures creuses
    if is_quiet_hours():
        logger.info("⏸️ Auto-repair skipped (quiet hours)")
        return {"status": "skipped", "reason": "quiet_hours"}

    logger.info("🔧 Starting scheduled auto-repair check...")

    # 1. Check health of all sources
    health = check_sources_health()

    healthy = [s for s, r in health.items() if r.get("status") == "ok"]
    broken = [s for s, r in health.items() if r.get("needs_repair")]

    logger.info(f"Health check: {len(healthy)} healthy, {len(broken)} need repair")

    if not broken:
        logger.info("✅ All sources healthy, nothing to repair")
        return {
            "status": "all_healthy",
            "healthy_sources": healthy,
            "duration_seconds": (datetime.utcnow() - start_time).total_seconds()
        }

    # 2. Diagnose and repair broken sources
    from app.services.scraping_service import SOURCE_LISTING_URLS

    repairs = []
    for source in broken:
        urls = SOURCE_LISTING_URLS.get(source, [])
        if not urls:
            repairs.append({"source": source, "status": "no_urls"})
            continue

        try:
            logger.info(f"🔍 Diagnosing {source}...")
            result = diagnose_source(source, urls[0])
            repairs.append(result)

            if result.get("fix_applied"):
                logger.info(f"✅ {source}: fix applied (confidence: {result.get('confidence', 0):.0%})")
            elif result.get("confidence", 0) > 0:
                logger.info(f"⚠️ {source}: needs manual fix (confidence: {result.get('confidence', 0):.0%})")
            else:
                logger.warning(f"❌ {source}: diagnosis failed")

        except Exception as e:
            logger.error(f"Error diagnosing {source}: {e}")
            repairs.append({"source": source, "status": "error", "error": str(e)[:100]})

    # Summary
    fixes_applied = [r for r in repairs if r.get("fix_applied")]
    needs_manual = [r for r in repairs if r.get("confidence", 0) > 0.5 and not r.get("fix_applied")]

    duration = (datetime.utcnow() - start_time).total_seconds()

    logger.info(
        f"🔧 Auto-repair complete: {len(fixes_applied)} auto-fixed, "
        f"{len(needs_manual)} need manual attention, "
        f"duration: {duration:.1f}s"
    )

    return {
        "status": "completed",
        "healthy_sources": healthy,
        "broken_sources": broken,
        "repairs": repairs,
        "auto_fixed": [r["source"] for r in fixes_applied],
        "needs_manual": [r["source"] for r in needs_manual],
        "duration_seconds": round(duration, 1)
    }


def manual_repair(source: str, autofix: bool = False) -> Dict[str, Any]:
    """
    Répare manuellement une source spécifique.
    """
    from app.services.scraping_service import SOURCE_LISTING_URLS
    from app.services.scraping_autorepair import (
        fetch_page_with_proxy,
        get_collector_code,
        analyze_with_ai,
        apply_fix,
    )

    urls = SOURCE_LISTING_URLS.get(source, [])
    if not urls:
        return {"source": source, "status": "no_urls"}

    listing_url = urls[0]

    # Fetch and analyze
    html, status = fetch_page_with_proxy(listing_url)
    if status == 0:
        return {"source": source, "status": "fetch_error", "error": html}

    collector_code = get_collector_code(source)
    if not collector_code:
        return {"source": source, "status": "no_collector"}

    error_msg = f"HTTP {status}" if status >= 400 else "No products extracted"
    analysis = analyze_with_ai(
        source=source,
        error_message=error_msg,
        html_sample=html,
        collector_code=collector_code,
        listing_url=listing_url
    )

    if "error" in analysis:
        return {"source": source, "status": "analysis_error", "error": analysis["error"]}

    # Apply fix if requested
    fix_applied = False
    if autofix and analysis.get("fixed_code"):
        fix_applied = apply_fix(source, analysis["fixed_code"])

    return {
        "source": source,
        "status": "diagnosed",
        "diagnosis": analysis.get("diagnosis"),
        "changes_detected": analysis.get("changes_detected", []),
        "fix_description": analysis.get("fix_description"),
        "confidence": analysis.get("confidence"),
        "fix_applied": fix_applied,
        "has_fix": bool(analysis.get("fixed_code")),
    }
