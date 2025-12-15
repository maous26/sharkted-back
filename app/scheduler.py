"""
Scheduler - Configuration des jobs planifiés.
"""
import os
from datetime import datetime, timedelta, timezone
from rq_scheduler import Scheduler
import redis

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def setup_scheduled_jobs():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")
    
    # Annuler les jobs existants
    for job in scheduler.get_jobs():
        scheduler.cancel(job)
    
    # 1. Scraping sources existantes (Courir, JD, etc.) - 30 min
    from app.jobs_scraping import scheduled_scraping
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        func=scheduled_scraping,
        interval=1800,
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: scraping every 30 min")
    
    # 2. KITH scraping - toutes les 2 heures
    from app.jobs_kith import collect_all_kith
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=10),
        func=collect_all_kith,
        interval=7200,  # 2 heures
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: KITH every 2 hours")
    
    # 3. Rescraping Vinted stats - 15 min
    from app.jobs_scoring import rescore_deals_batch
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        func=rescore_deals_batch,
        args=[50, False],
        interval=900,
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: rescore deals every 15 min")
    
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
    
    logger.info("All scheduled jobs configured")
    return scheduler


def get_scheduled_jobs_info():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")
    
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "func_name": job.func_name,
            "scheduled_for": job.meta.get("scheduled_for"),
            "interval": job.meta.get("interval"),
        })
    
    return jobs
