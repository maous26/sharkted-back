"""
Auto-Repair Service for Scraping - Uses AI to fix broken scrapers.

Ce service détecte les sources qui échouent et utilise Claude pour:
1. Analyser pourquoi le scraper échoue
2. Proposer des corrections
3. Optionnellement appliquer les fixes automatiquement

Workflow:
1. Monitor: surveille les scraping_logs pour détecter les erreurs
2. Diagnose: récupère le HTML de la source et l'envoie à Claude
3. Fix: Claude analyse et propose une correction du code
4. Apply: applique le fix si autofix=True
"""

import os
import re
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from loguru import logger

import httpx
from anthropic import Anthropic

from app.db.session import SessionLocal
from app.services.scraping_orchestrator import get_proxy


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


def get_recent_failures(hours: int = 24) -> List[Dict]:
    """
    Récupère les sources qui ont échoué récemment.
    """
    session = SessionLocal()
    try:
        # Query scraping_logs for recent failures
        query = """
            SELECT source_slug, status, errors, started_at
            FROM scraping_logs
            WHERE started_at > NOW() - INTERVAL '%s hours'
            AND status IN ('error', 'blocked')
            ORDER BY started_at DESC
        """
        from sqlalchemy import text
        result = session.execute(text(query % hours))

        failures = []
        for row in result:
            failures.append({
                'source': row.source_slug,
                'status': row.status,
                'errors': row.errors,
                'timestamp': row.started_at.isoformat() if row.started_at else None
            })
        return failures
    finally:
        session.close()


def fetch_page_with_proxy(url: str) -> tuple[str, int]:
    """
    Fetch a page using Web Unlocker proxy.
    Returns (html_content, status_code)
    """
    proxy_config = get_proxy("web_unlocker")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        with httpx.Client(
            timeout=60,
            follow_redirects=True,
            proxy=proxy_config.get("http") if proxy_config else None,
            verify=False if proxy_config else True,
        ) as client:
            resp = client.get(url, headers=headers)
            return resp.text, resp.status_code
    except Exception as e:
        return str(e), 0


def get_collector_code(source: str) -> Optional[str]:
    """
    Récupère le code du collector pour une source.
    """
    collector_path = f"/app/app/collectors/sources/{source}.py"
    try:
        with open(collector_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None


def analyze_with_ai(
    source: str,
    error_message: str,
    html_sample: str,
    collector_code: str,
    listing_url: str
) -> Dict[str, Any]:
    """
    Utilise Claude pour analyser pourquoi le scraper échoue.
    """
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured"}

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Truncate HTML to fit context
    html_truncated = html_sample[:50000] if len(html_sample) > 50000 else html_sample

    prompt = f"""Tu es un expert en web scraping Python. Un scraper a cessé de fonctionner.

## Source: {source}
## URL: {listing_url}
## Erreur: {error_message}

## Code actuel du collector:
```python
{collector_code}
```

## Échantillon HTML de la page (tronqué):
```html
{html_truncated[:20000]}
```

## Ta tâche:
1. Analyse pourquoi le scraper échoue
2. Identifie les changements dans la structure HTML
3. Propose une version corrigée du code

Réponds en JSON avec ce format:
{{
    "diagnosis": "Explication courte du problème",
    "changes_detected": ["liste des changements détectés dans le HTML"],
    "fix_description": "Description de la correction proposée",
    "fixed_code": "Code Python corrigé complet du collector (pas juste un extrait)",
    "confidence": 0.0-1.0,
    "test_suggestions": ["suggestions pour tester le fix"]
}}

IMPORTANT: Le fixed_code doit être le fichier complet, pas juste les parties modifiées.
"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )

        # Extract JSON from response
        response_text = response.content[0].text

        # Try to parse JSON
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            result = json.loads(json_match.group())
            return result
        else:
            return {"error": "Could not parse AI response", "raw": response_text[:1000]}

    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        return {"error": str(e)}


def apply_fix(source: str, fixed_code: str) -> bool:
    """
    Applique le code corrigé.
    """
    collector_path = f"/app/app/collectors/sources/{source}.py"
    backup_path = f"{collector_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        # Backup original
        with open(collector_path, "r") as f:
            original = f.read()
        with open(backup_path, "w") as f:
            f.write(original)

        # Validate new code syntax
        compile(fixed_code, collector_path, 'exec')

        # Write new code
        with open(collector_path, "w") as f:
            f.write(fixed_code)

        logger.info(f"Applied fix to {source}, backup at {backup_path}")
        return True

    except SyntaxError as e:
        logger.error(f"Syntax error in fixed code: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to apply fix: {e}")
        return False


async def diagnose_and_repair(
    source: str,
    listing_url: str,
    autofix: bool = False
) -> Dict[str, Any]:
    """
    Diagnostique et répare un scraper cassé.

    Args:
        source: Nom de la source (courir, asos, etc.)
        listing_url: URL de listing à tester
        autofix: Si True, applique automatiquement le fix

    Returns:
        Dict avec diagnostic et fix proposé
    """
    logger.info(f"Starting diagnosis for {source}")

    # 1. Fetch the page
    html, status = fetch_page_with_proxy(listing_url)

    if status == 0:
        return {
            "source": source,
            "status": "fetch_error",
            "error": html,  # Contains error message
        }

    # 2. Get current collector code
    collector_code = get_collector_code(source)
    if not collector_code:
        return {
            "source": source,
            "status": "no_collector",
            "error": f"No collector found for {source}"
        }

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
        return {
            "source": source,
            "status": "analysis_error",
            "error": analysis["error"]
        }

    # 4. Optionally apply fix
    fix_applied = False
    if autofix and analysis.get("fixed_code") and analysis.get("confidence", 0) > 0.7:
        fix_applied = apply_fix(source, analysis["fixed_code"])

    return {
        "source": source,
        "status": "diagnosed",
        "http_status": status,
        "diagnosis": analysis.get("diagnosis"),
        "changes_detected": analysis.get("changes_detected", []),
        "fix_description": analysis.get("fix_description"),
        "confidence": analysis.get("confidence"),
        "fix_applied": fix_applied,
        "test_suggestions": analysis.get("test_suggestions", []),
        "has_fix": bool(analysis.get("fixed_code"))
    }


def check_all_sources_health() -> Dict[str, Any]:
    """
    Vérifie la santé de toutes les sources actives.
    Utilise les logs de scraping récents comme source de vérité.
    """
    from app.core.source_policy import SOURCE_POLICIES
    from app.db.session import SessionLocal
    from sqlalchemy import text
    from datetime import datetime, timedelta

    results = {}
    session = SessionLocal()

    try:
        # Check recent scraping logs (last 2 hours)
        query = text("""
            SELECT source_slug, status, deals_found, started_at
            FROM scraping_logs
            WHERE started_at > NOW() - INTERVAL '2 hours'
            ORDER BY started_at DESC
        """)
        logs = session.execute(query).fetchall()

        # Group by source, take most recent
        source_status = {}
        for log in logs:
            source = log.source_slug
            if source not in source_status:
                source_status[source] = {
                    "status": log.status,
                    "deals_found": log.deals_found,
                    "last_run": log.started_at.isoformat() if log.started_at else None
                }

        # Check each enabled source
        for source, policy in SOURCE_POLICIES.items():
            if not policy.enabled:
                results[source] = {"status": "disabled"}
                continue

            if source in source_status:
                log = source_status[source]
                is_ok = log["status"] in ("success", "completed", "partial") and log["deals_found"] > 0
                results[source] = {
                    "status": "ok" if is_ok else "degraded",
                    "last_status": log["status"],
                    "deals_found": log["deals_found"],
                    "last_run": log["last_run"],
                    "needs_repair": not is_ok
                }
            else:
                # No recent logs - needs checking
                results[source] = {
                    "status": "unknown",
                    "needs_repair": False  # Don't auto-repair if just no recent data
                }

        # Special handling for Kith (uses separate job)
        if "kith" in results and results["kith"].get("status") == "unknown":
            # Check if Kith has deals
            count_query = text("SELECT COUNT(*) FROM deals WHERE source = 'kith'")
            count = session.execute(count_query).scalar()
            if count and count > 0:
                results["kith"] = {"status": "ok", "deals_count": count, "needs_repair": False}

    finally:
        session.close()

    return results
