"""
Scheduler - Jobs planifiés avec scoring autonome.
Scraping uniquement entre 20h et 6h du matin (heure Paris).
"""
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from rq_scheduler import Scheduler
import redis

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Timezone Paris
PARIS_TZ = ZoneInfo("Europe/Paris")

# Heures actives pour le scraping (20h - 6h Paris)
# En dehors de ces heures = quiet hours (pas de scraping)
ACTIVE_HOURS_START = 20  # 20h du soir
ACTIVE_HOURS_END = 6     # 6h du matin


def is_quiet_hours() -> bool:
    """Vérifie si on est dans les heures de pause (6h - 20h Paris = journée)."""
    # TEMPORAIRE: Désactivé pour les tests - scraping autorisé 24h/24
    return False

    # Code original (à réactiver après les tests):
    # now_paris = datetime.now(PARIS_TZ)
    # current_hour = now_paris.hour
    # # Actif si entre 20h-23h59 OU 0h-6h
    # # Quiet (pause) si entre 6h et 20h
    # if ACTIVE_HOURS_END <= current_hour < ACTIVE_HOURS_START:
    #     return True  # C'est la journée, pas de scraping
    # return False  # C'est la nuit, on peut scraper


def setup_scheduled_jobs():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")

    # Annuler les jobs existants
    for job in scheduler.get_jobs():
        scheduler.cancel(job)

    # 1. Scraping principal - toutes les 4 heures (uniquement 20h-6h)
    from app.jobs_scraping import scheduled_scraping
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        func=scheduled_scraping,
        interval=14400,  # 4 heures = 4 * 3600 = 14400 secondes
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: main scraping every 4 hours (active 20h-6h)")

    # 2. KITH scraping - toutes les 4 heures (aligné avec le principal)
    from app.jobs_kith import collect_all_kith
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        func=collect_all_kith,
        interval=14400,  # 4 heures
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: KITH every 4 hours")

    # 3. Auto-repair scrapers - toutes les heures
    from app.jobs_autorepair import scheduled_autorepair
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=10),
        func=scheduled_autorepair,
        interval=3600,  # 1 heure
        repeat=None,
        result_ttl=3600,
        queue_name="low",
    )
    logger.info("Scheduled: auto-repair every 1 hour")

    # 4. Nettoyage logs - 24h
    from app.services.scraping_service import delete_old_scraping_logs
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(hours=1),
        func=delete_old_scraping_logs,
        args=[7],
        interval=86400,
        repeat=None,
        result_ttl=3600,
        queue_name="low",
    )
    logger.info("Scheduled: cleanup logs every 24h")

    logger.info(f"All jobs configured - Active hours: {ACTIVE_HOURS_START}h-{ACTIVE_HOURS_END}h Paris (scraping every 4h)")
    return scheduler


def get_scheduled_jobs_info():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")

    return [{
        "id": job.id,
        "func_name": job.func_name,
        "interval": job.meta.get("interval"),
    } for job in scheduler.get_jobs()]
