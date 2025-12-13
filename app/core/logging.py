"""
Configuration du logging structuré JSON.

Chaque log contient:
- timestamp: ISO8601
- level: DEBUG/INFO/WARNING/ERROR/CRITICAL
- message: message principal
- source: source du collector (optionnel)
- url: URL traitée (optionnel)
- trace_id: ID de traçage pour corrélation (optionnel)
- duration_ms: durée en ms (optionnel)
- extra: données additionnelles
"""
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional

# Context variable pour le trace_id (propagé à travers les appels)
_trace_id: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def get_trace_id() -> Optional[str]:
    """Récupère le trace_id courant."""
    return _trace_id.get()


def set_trace_id(trace_id: Optional[str] = None) -> str:
    """Définit un trace_id. Génère un nouveau si non fourni."""
    if trace_id is None:
        trace_id = str(uuid.uuid4())[:8]
    _trace_id.set(trace_id)
    return trace_id


class JSONFormatter(logging.Formatter):
    """Formatter qui produit des logs en JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Ajouter le trace_id si disponible
        trace_id = get_trace_id()
        if trace_id:
            log_data["trace_id"] = trace_id

        # Ajouter les extras du record
        for key in ("source", "url", "duration_ms", "job_id", "status_code", "error_type"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)

        # Exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Données extra génériques
        if hasattr(record, "extra_data") and record.extra_data:
            log_data["extra"] = record.extra_data

        return json.dumps(log_data, ensure_ascii=False, default=str)


class StructuredLogger:
    """
    Logger structuré avec méthodes helper pour le contexte collector.
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(
        self,
        level: int,
        message: str,
        source: Optional[str] = None,
        url: Optional[str] = None,
        duration_ms: Optional[float] = None,
        job_id: Optional[str] = None,
        error_type: Optional[str] = None,
        status_code: Optional[int] = None,
        exc_info: bool = False,
        **extra
    ):
        """Log avec contexte structuré."""
        extra_dict = {k: v for k, v in extra.items() if v is not None}

        record_extra = {}
        if source:
            record_extra["source"] = source
        if url:
            record_extra["url"] = url[:200]  # Tronquer les URLs longues
        if duration_ms is not None:
            record_extra["duration_ms"] = round(duration_ms, 2)
        if job_id:
            record_extra["job_id"] = job_id
        if error_type:
            record_extra["error_type"] = error_type
        if status_code:
            record_extra["status_code"] = status_code
        if extra_dict:
            record_extra["extra_data"] = extra_dict

        self._logger.log(level, message, exc_info=exc_info, extra=record_extra)

    def debug(self, message: str, **kwargs):
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs):
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, exc_info: bool = True, **kwargs):
        self._log(logging.ERROR, message, exc_info=exc_info, **kwargs)

    def critical(self, message: str, exc_info: bool = True, **kwargs):
        self._log(logging.CRITICAL, message, exc_info=exc_info, **kwargs)

    # Méthodes spécialisées pour le pipeline

    def collect_start(self, source: str, url: str, job_id: Optional[str] = None):
        """Log le début d'une collecte."""
        self.info("Collection started", source=source, url=url, job_id=job_id)

    def collect_success(
        self,
        source: str,
        url: str,
        duration_ms: float,
        job_id: Optional[str] = None,
        items_count: int = 1,
    ):
        """Log une collecte réussie."""
        self.info(
            "Collection successful",
            source=source,
            url=url,
            duration_ms=duration_ms,
            job_id=job_id,
            items_count=items_count,
        )

    def collect_error(
        self,
        source: str,
        url: str,
        error: Exception,
        duration_ms: Optional[float] = None,
        job_id: Optional[str] = None,
    ):
        """Log une erreur de collecte."""
        error_type = type(error).__name__
        status_code = getattr(error, "status_code", None)

        self.error(
            f"Collection failed: {error}",
            source=source,
            url=url,
            duration_ms=duration_ms,
            job_id=job_id,
            error_type=error_type,
            status_code=status_code,
            exc_info=False,  # L'erreur est déjà dans le message
        )

    def persist_success(self, source: str, external_id: str, action: str):
        """Log une persistance réussie."""
        self.info(
            f"Deal {action}",
            source=source,
            external_id=external_id,
            action=action,
        )

    def persist_error(self, source: str, external_id: str, error: Exception):
        """Log une erreur de persistance."""
        self.error(
            f"Persistence failed: {error}",
            source=source,
            external_id=external_id,
            error_type=type(error).__name__,
        )


def setup_logging(level: str = "INFO"):
    """
    Configure le logging pour l'application.

    Args:
        level: Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Supprimer les handlers existants
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Handler stdout avec JSON formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)

    # Réduire le bruit des libs externes
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """Obtient un logger structuré."""
    return StructuredLogger(name)


def timed(logger: Optional[StructuredLogger] = None):
    """
    Décorateur pour mesurer et logger la durée d'une fonction.

    Usage:
        @timed(logger)
        def my_function():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                if logger:
                    logger.debug(
                        f"{func.__name__} completed",
                        duration_ms=duration_ms,
                    )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                if logger:
                    logger.error(
                        f"{func.__name__} failed",
                        duration_ms=duration_ms,
                        error_type=type(e).__name__,
                    )
                raise
        return wrapper
    return decorator
