"""
Scheduler - Jobs planifiés avec scoring autonome + StockX via Web Unlocker.
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
    
    # 3. Nettoyage logs - 24h
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
    
    # 4. Scoring des nouveaux deals (avec Vinted Sniper) - toutes les 10 min
    from app.jobs_scoring import score_new_deals
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        func=score_new_deals,
        kwargs={"limit": 20},
        interval=600,  # 10 min
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: Vinted scoring (new deals) every 10 min")
    
    logger.info("All jobs configured (Scraping + Vinted Scoring)")
    return scheduler


def get_scheduled_jobs_info():
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")
    
    return [{
        "id": job.id,
        "func_name": job.func_name,
        "interval": job.meta.get("interval"),
    } for job in scheduler.get_jobs()]
