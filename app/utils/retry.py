"""
Utilitaires de retry avec backoff exponentiel.

Supporte:
- Backoff exponentiel avec jitter
- Filtrage des exceptions retryable
- Logging des tentatives
- Décorateur et fonction
"""
import random
import time
from functools import wraps
from typing import Callable, Type, Tuple, Optional, TypeVar, Any

from app.core.logging import get_logger
from app.core.exceptions import is_retryable, CollectorError

logger = get_logger(__name__)

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
    source: Optional[str] = None,
) -> T:
    """
    Exécute une fonction avec retry et backoff exponentiel.

    Args:
        fn: Fonction à exécuter (sans arguments)
        retries: Nombre de retries maximum
        base_delay: Délai initial en secondes
        max_delay: Délai maximum en secondes
        retry_on: Tuple d'exceptions sur lesquelles retry (None = utilise is_retryable)
        source: Nom de la source pour le logging

    Returns:
        Résultat de fn()

    Raises:
        L'exception de la dernière tentative si toutes échouent
    """
    last_err = None

    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e

            # Vérifier si on doit retry
            should_retry = False
            if retry_on is not None:
                should_retry = isinstance(e, retry_on)
            else:
                should_retry = is_retryable(e)

            # Dernière tentative ou non retryable
            if attempt >= retries or not should_retry:
                logger.warning(
                    f"Retry exhausted after {attempt + 1} attempts",
                    source=source,
                    error_type=type(e).__name__,
                    attempt=attempt + 1,
                    max_attempts=retries + 1,
                )
                raise

            # Calculer le délai avec backoff + jitter
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay = delay * (0.7 + random.random() * 0.6)

            logger.info(
                f"Retry attempt {attempt + 1}/{retries + 1}, waiting {delay:.2f}s",
                source=source,
                error_type=type(e).__name__,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
            )

            time.sleep(delay)

    raise last_err  # Ne devrait jamais arriver


def retry(
    retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
    source: Optional[str] = None,
):
    """
    Décorateur de retry avec backoff exponentiel.

    Usage:
        @retry(retries=3, source="adidas")
        def fetch_product(url):
            ...

        @retry(retry_on=(TimeoutError, NetworkError))
        def fetch_data():
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            return with_retry(
                lambda: fn(*args, **kwargs),
                retries=retries,
                base_delay=base_delay,
                max_delay=max_delay,
                retry_on=retry_on,
                source=source or getattr(fn, "__module__", None),
            )
        return wrapper
    return decorator


def retry_on_network_errors(retries: int = 3, source: Optional[str] = None):
    """
    Décorateur spécialisé pour les erreurs réseau.
    Retry sur: Timeout, ConnectionError, erreurs 5xx.
    """
    import requests.exceptions
    from app.core.exceptions import NetworkError, TimeoutError, HTTPError

    def should_retry(e: Exception) -> bool:
        # Erreurs réseau standard
        if isinstance(e, (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            NetworkError,
            TimeoutError,
        )):
            return True
        # Erreurs HTTP 5xx
        if isinstance(e, HTTPError) and e.status_code >= 500:
            return True
        # Utiliser le flag retryable si disponible
        if isinstance(e, CollectorError):
            return e.retryable
        return False

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            last_err = None
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt >= retries or not should_retry(e):
                        raise

                    delay = min(10.0, 0.5 * (2 ** attempt))
                    delay = delay * (0.7 + random.random() * 0.6)

                    logger.info(
                        f"Network retry {attempt + 1}/{retries + 1}",
                        source=source,
                        error_type=type(e).__name__,
                        delay_s=round(delay, 2),
                    )
                    time.sleep(delay)

            raise last_err
        return wrapper
    return decorator
