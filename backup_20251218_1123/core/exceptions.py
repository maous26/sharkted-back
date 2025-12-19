"""
Hiérarchie d'exceptions pour le pipeline de collection.

Permet de distinguer:
- Erreurs transitoires (retry possible)
- Erreurs permanentes (pas de retry)
- Erreurs de blocage (site protégé)
"""
from typing import Optional


class CollectorError(Exception):
    """Exception de base pour tous les collectors."""

    def __init__(
        self,
        message: str,
        source: Optional[str] = None,
        url: Optional[str] = None,
        retryable: bool = False,
    ):
        self.source = source
        self.url = url
        self.retryable = retryable
        super().__init__(message)

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.source:
            parts.append(f"source={self.source}")
        if self.url:
            parts.append(f"url={self.url[:50]}...")
        return " | ".join(parts)


# =============================================================================
# ERREURS RÉSEAU (généralement retryable)
# =============================================================================

class NetworkError(CollectorError):
    """Erreur réseau générique (timeout, DNS, connection refused)."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, retryable=True, **kwargs)


class TimeoutError(NetworkError):
    """Timeout lors de la requête."""
    pass


class ConnectionError(NetworkError):
    """Impossible de se connecter au serveur."""
    pass


# =============================================================================
# ERREURS HTTP
# =============================================================================

class HTTPError(CollectorError):
    """Erreur HTTP avec code de status."""

    def __init__(
        self,
        message: str,
        status_code: int,
        **kwargs
    ):
        self.status_code = status_code
        # 5xx sont retryable, 4xx non (sauf 429)
        retryable = status_code >= 500 or status_code == 429
        super().__init__(message, retryable=retryable, **kwargs)

    def __str__(self) -> str:
        return f"HTTP {self.status_code}: {super().__str__()}"


class RateLimitError(HTTPError):
    """Rate limit atteint (429 ou blocage soft)."""

    def __init__(self, message: str = "Rate limit exceeded", **kwargs):
        super().__init__(message, status_code=429, **kwargs)
        self.retryable = True  # Toujours retryable avec backoff


class BlockedError(HTTPError):
    """
    Requête bloquée par protection anti-bot (Akamai, Cloudflare, etc.)

    Généralement status 403, parfois 503.
    Non retryable avec la même méthode - nécessite escalade (proxy, browser).
    """

    def __init__(self, message: str = "Blocked by anti-bot protection", **kwargs):
        status_code = kwargs.pop("status_code", 403)
        super().__init__(message, status_code=status_code, **kwargs)
        self.retryable = False  # Pas retryable sans changement de stratégie


class NotFoundError(HTTPError):
    """Ressource non trouvée (404)."""

    def __init__(self, message: str = "Resource not found", **kwargs):
        super().__init__(message, status_code=404, **kwargs)
        self.retryable = False


# =============================================================================
# ERREURS DE PARSING
# =============================================================================

class ParseError(CollectorError):
    """Erreur lors du parsing des données."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, retryable=False, **kwargs)


class JSONParseError(ParseError):
    """Erreur lors du parsing JSON."""
    pass


class HTMLParseError(ParseError):
    """Erreur lors du parsing HTML."""
    pass


class DataExtractionError(ParseError):
    """Données attendues non trouvées dans la réponse."""
    pass


# =============================================================================
# ERREURS DE VALIDATION
# =============================================================================

class ValidationError(CollectorError):
    """Données extraites invalides."""

    def __init__(self, message: str, field: Optional[str] = None, **kwargs):
        self.field = field
        super().__init__(message, retryable=False, **kwargs)


# =============================================================================
# ERREURS DE PERSISTANCE
# =============================================================================

class PersistenceError(CollectorError):
    """Erreur lors de la sauvegarde en base."""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, retryable=True, **kwargs)  # DB peut être temporairement indisponible


class DuplicateError(PersistenceError):
    """Deal déjà existant (peut être ignoré)."""

    def __init__(self, message: str = "Deal already exists", **kwargs):
        super().__init__(message, **kwargs)
        self.retryable = False


# =============================================================================
# HELPERS
# =============================================================================

def is_retryable(exc: Exception) -> bool:
    """Vérifie si une exception est retryable."""
    if isinstance(exc, CollectorError):
        return exc.retryable
    # Exceptions standard considérées retryable
    import requests
    if isinstance(exc, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
    )):
        return True
    return False
