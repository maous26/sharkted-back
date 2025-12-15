"""
Scheduler - Configuration des jobs planifiés.

Nouveau flow batch (toutes les 15 min):
1. Scraper les sources -> deals en attente
2. Batch Vinted -> stats pour les deals en attente  
3. Batch scoring -> score et filtre (supprime si < 60)
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
    
    # 1. CYCLE COMPLET toutes les 15 min
    # (scraping sources + batch Vinted + batch scoring)
    from app.jobs_scraping import scheduled_scraping
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=2),
        func=scheduled_scraping,
        interval=900,  # 15 minutes
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: full cycle (scrape+vinted+score) every 15 min")
    
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
    
    # 3. Batch Vinted supplémentaire (rattrapage) - toutes les 30 min
    from app.jobs_scraping import run_vinted_batch
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=7),
        func=run_vinted_batch,
        interval=1800,  # 30 minutes
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: Vinted batch (catch-up) every 30 min")
    
    # 4. Batch scoring supplémentaire (rattrapage) - toutes les 20 min
    from app.jobs_scraping import run_scoring_batch
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=12),
        func=run_scoring_batch,
        interval=1200,  # 20 minutes
        repeat=None,
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled: scoring batch (catch-up) every 20 min")
    
    # 5. Nettoyage logs - 24h
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
    
    logger.info("All scheduled jobs configured (batch mode)")
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
