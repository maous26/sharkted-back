"""
Scheduler - Configuration des jobs planifiés.

Ce module configure les jobs qui s'exécutent automatiquement:
- Scraping périodique des sources
- Nettoyage des vieux logs
- Reset des stats 24h
"""
import os
from datetime import datetime, timedelta, timezone
from rq_scheduler import Scheduler
import redis

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


def setup_scheduled_jobs():
    """
    Configure tous les jobs planifiés.
    À appeler au démarrage de l'application.
    """
    redis_conn = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=redis_conn, queue_name="default")
    
    # Annuler les jobs existants pour éviter les doublons
    for job in scheduler.get_jobs():
        scheduler.cancel(job)
    
    # 1. Scraping automatique toutes les 30 minutes
    from app.jobs_scraping import scheduled_scraping
    
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        func=scheduled_scraping,
        interval=1800,  # 30 minutes
        repeat=None,  # Répéter indéfiniment
        result_ttl=3600,
        queue_name="default",
    )
    logger.info("Scheduled job: scraping every 30 minutes")
    
    # 2. Nettoyage des vieux logs toutes les 24h
    from app.services.scraping_service import delete_old_scraping_logs
    
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc) + timedelta(hours=1),
        func=delete_old_scraping_logs,
        args=[7],  # Supprimer les logs > 7 jours
        interval=86400,  # 24 heures
        repeat=None,
        result_ttl=3600,
        queue_name="low",
    )
    logger.info("Scheduled job: cleanup logs every 24h")
    
    logger.info("All scheduled jobs configured")
    return scheduler


def get_scheduled_jobs_info():
    """Retourne les infos sur les jobs planifiés."""
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
