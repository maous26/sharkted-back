"""
Scheduler - Jobs planifiés avec scoring autonome.
Pas de scraping entre minuit et 7h du matin (heure Paris).
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

# Heures de pause (pas de scraping)
QUIET_HOURS_START = 0   # Minuit
QUIET_HOURS_END = 7     # 7h du matin


def is_quiet_hours() -> bool:
    """Vérifie si on est dans les heures de pause (minuit - 7h Paris)."""
    now_paris = datetime.now(PARIS_TZ)
    current_hour = now_paris.hour
    return QUIET_HOURS_START <= current_hour < QUIET_HOURS_END


def setup_scheduled_jobs():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")

    # Annuler les jobs existants
    for job in scheduler.get_jobs():
        scheduler.cancel(job)

    # 1. Scraping principal - toutes les 15 min
    from app.jobs_scraping import scheduled_scraping
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        func=scheduled_scraping,
        interval=900,  # 15 min
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: main scraping every 15 min")

    # 2. KITH scraping - toutes les 2 heures
    from app.jobs_kith import collect_all_kith
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        func=collect_all_kith,
        interval=7200,
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: KITH every 2 hours")

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

    logger.info(f"All jobs configured - Quiet hours: {QUIET_HOURS_START}h-{QUIET_HOURS_END}h Paris")
    return scheduler


def get_scheduled_jobs_info():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")

    return [{
        "id": job.id,
        "func_name": job.func_name,
        "interval": job.meta.get("interval"),
    } for job in scheduler.get_jobs()]
