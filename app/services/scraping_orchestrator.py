"""
Scraping Orchestrator - Orchestrateur intelligent avec fallback automatique.

Stratégie de lancement (Phase 1 - Sans proxies résidentiels):
- Scraper uniquement les sites accessibles sans proxy ou avec datacenter
- Les sites nécessitant des proxies résidentiels sont désactivés

Stratégie future (Phase 2 - Avec proxies résidentiels):
- Activer Footlocker, Adidas, Zalando quand les proxies seront configurés

L'orchestrateur:
- Choisit la meilleure méthode selon la cible et le taux d'erreurs
- Bascule automatiquement en cas d'échec
- Log les métriques pour optimisation
- Minimise les coûts (proxies uniquement quand nécessaire)
"""
import time
import random
import hashlib
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any, Callable
from threading import Lock
import json

import cloudscraper
import requests
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# ENUMS & CONSTANTS
# =============================================================================

class ScrapingMethod(str, Enum):
    """Méthodes de scraping disponibles, par ordre de coût croissant."""
    HTTP_DIRECT = "http_direct"           # Sans proxy
    HTTP_DATACENTER = "http_datacenter"   # Proxy datacenter (cheap)
    HTTP_RESIDENTIAL = "http_residential" # Proxy résidentiel (premium) - Phase 2
    BROWSER_RESIDENTIAL = "browser"       # Playwright + proxy résidentiel - Phase 2


class TargetProtection(str, Enum):
    """Niveau de protection anti-bot de la cible."""
    NONE = "none"           # Pas de protection
    BASIC = "basic"         # Rate limiting simple
    CLOUDFLARE = "cloudflare"  # Cloudflare (cloudscraper suffit souvent)
    AKAMAI = "akamai"       # Akamai Bot Manager (dur) - Nécessite résidentiel
    PERIMETERX = "perimeterx" # PerimeterX (dur) - Nécessite résidentiel
    CUSTOM = "custom"       # Protection custom (à analyser)


class ErrorType(str, Enum):
    """Types d'erreurs pour décision de fallback."""
    SUCCESS = "success"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    HTTP_400 = "http_400"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"
    NETWORK = "network"
    BLOCKED = "blocked"
    CAPTCHA = "captcha"
    JS_CHALLENGE = "js_challenge"


# Seuils de décision
FALLBACK_THRESHOLDS = {
    "403_count_before_escalate": 3,
    "429_backoff_seconds": 60,
    "timeout_count_before_escalate": 2,
    "success_rate_min": 0.7,
    "window_minutes": 10,
}


# =============================================================================
# PROXY CONFIGURATION
# =============================================================================

@dataclass
class ProxyConfig:
    """Configuration d'un provider de proxy."""
    provider: str
    type: str
    endpoint: str
    username: str
    password: str
    country: str = "FR"
    rotation: str = "rotating"
    session_ttl: int = 300
    enabled: bool = True
    
    def to_url(self, session_id: Optional[str] = None) -> str:
        user = self.username
        if session_id and self.rotation == "sticky":
            user = f"{self.username}-session-{session_id}"
        return f"http://{user}:{self.password}@{self.endpoint}"
    
    def to_dict(self) -> Dict[str, str]:
        url = self.to_url()
        return {"http": url, "https": url}


@dataclass 
class ProxyPool:
    """Pool de proxies par niveau."""
    datacenter: List[ProxyConfig] = field(default_factory=list)
    residential: List[ProxyConfig] = field(default_factory=list)
    
    def get_proxy(self, level: str, session_id: Optional[str] = None) -> Optional[Dict[str, str]]:
        pool = self.datacenter if level == "datacenter" else self.residential
        enabled = [p for p in pool if p.enabled]
        if not enabled:
            return None
        proxy = random.choice(enabled)
        return proxy.to_dict()
    
    def has_residential(self) -> bool:
        """Vérifie si des proxies résidentiels sont configurés."""
        return len([p for p in self.residential if p.enabled]) > 0


_proxy_pool = ProxyPool()


def configure_proxies(config: Dict[str, Any]) -> None:
    global _proxy_pool
    
    if "datacenter" in config:
        _proxy_pool.datacenter = [
            ProxyConfig(**p) for p in config["datacenter"]
        ]
    if "residential" in config:
        _proxy_pool.residential = [
            ProxyConfig(**p) for p in config["residential"]
        ]
    
    logger.info(
        "Proxy pool configured",
        datacenter_count=len(_proxy_pool.datacenter),
        residential_count=len(_proxy_pool.residential),
    )


def get_proxy(level: str, session_id: Optional[str] = None) -> Optional[Dict[str, str]]:
    return _proxy_pool.get_proxy(level, session_id)


def has_residential_proxies() -> bool:
    """Vérifie si des proxies résidentiels sont disponibles."""
    return _proxy_pool.has_residential()


# =============================================================================
# TARGET CONFIGURATION
# =============================================================================

@dataclass
class TargetConfig:
    """Configuration de scraping pour une cible (site)."""
    slug: str
    name: str
    base_url: str
    protection: TargetProtection = TargetProtection.NONE
    
    # Méthodes autorisées (ordre = préférence)
    allowed_methods: List[ScrapingMethod] = field(default_factory=lambda: [
        ScrapingMethod.HTTP_DIRECT,
        ScrapingMethod.HTTP_DATACENTER,
    ])
    
    # Nécessite des proxies résidentiels ?
    requires_residential: bool = False
    
    # Rate limiting
    requests_per_second: float = 1.0
    burst_size: int = 3
    
    # Retry config
    max_retries: int = 3
    retry_delay_base: float = 2.0
    
    # Headers custom
    custom_headers: Dict[str, str] = field(default_factory=dict)
    
    # Activé dans la phase actuelle ?
    enabled: bool = True
    disabled_reason: Optional[str] = None


# =============================================================================
# CONFIGURATION DES CIBLES - PHASE 1 (Sans proxies résidentiels)
# =============================================================================

TARGET_CONFIGS: Dict[str, TargetConfig] = {
    # =========================================================================
    # SOURCES ACTIVES - Fonctionnent sans proxy ou avec datacenter
    # =========================================================================
    "courir": TargetConfig(
        slug="courir",
        name="Courir",
        base_url="https://www.courir.com",
        protection=TargetProtection.BASIC,
        allowed_methods=[
            ScrapingMethod.HTTP_DIRECT,
            ScrapingMethod.HTTP_DATACENTER,
        ],
        requires_residential=False,
        requests_per_second=2.0,
        enabled=True,
    ),
    "size": TargetConfig(
        slug="size",
        name="Size?",
        base_url="https://www.size.co.uk",
        protection=TargetProtection.BASIC,
        allowed_methods=[
            ScrapingMethod.HTTP_DIRECT,
            ScrapingMethod.HTTP_DATACENTER,
        ],
        requires_residential=False,
        requests_per_second=1.5,
        enabled=True,
    ),
    "jdsports": TargetConfig(
        slug="jdsports",
        name="JD Sports",
        base_url="https://www.jdsports.fr",
        protection=TargetProtection.BASIC,
        allowed_methods=[
            ScrapingMethod.HTTP_DIRECT,
            ScrapingMethod.HTTP_DATACENTER,
        ],
        requires_residential=False,
        requests_per_second=1.5,
        enabled=True,
    ),
    "vinted": TargetConfig(
        slug="vinted",
        name="Vinted",
        base_url="https://www.vinted.fr",
        protection=TargetProtection.CUSTOM,
        allowed_methods=[
            ScrapingMethod.HTTP_DIRECT,
            ScrapingMethod.HTTP_DATACENTER,
        ],
        requires_residential=False,
        requests_per_second=1.0,
        enabled=True,
    ),
    "snipes": TargetConfig(
        slug="snipes",
        name="Snipes",
        base_url="https://www.snipes.fr",
        protection=TargetProtection.CLOUDFLARE,
        allowed_methods=[
            ScrapingMethod.HTTP_DIRECT,
            ScrapingMethod.HTTP_DATACENTER,
        ],
        requires_residential=False,
        requests_per_second=1.0,
        enabled=True,
    ),
    
    # =========================================================================
    # SOURCES DÉSACTIVÉES - Nécessitent des proxies résidentiels (Phase 2)
    # =========================================================================
    "footlocker": TargetConfig(
        slug="footlocker",
        name="Foot Locker",
        base_url="https://www.footlocker.fr",
        protection=TargetProtection.AKAMAI,
        allowed_methods=[
            ScrapingMethod.HTTP_RESIDENTIAL,
            ScrapingMethod.BROWSER_RESIDENTIAL,
        ],
        requires_residential=True,
        requests_per_second=0.5,
        enabled=False,  # Désactivé Phase 1
        disabled_reason="Requires residential proxies (Akamai protection)",
    ),
    "adidas": TargetConfig(
        slug="adidas",
        name="Adidas",
        base_url="https://www.adidas.fr",
        protection=TargetProtection.AKAMAI,
        allowed_methods=[
            ScrapingMethod.HTTP_RESIDENTIAL,
            ScrapingMethod.BROWSER_RESIDENTIAL,
        ],
        requires_residential=True,
        requests_per_second=0.3,
        enabled=False,  # Désactivé Phase 1
        disabled_reason="Requires residential proxies (Akamai protection)",
    ),
    "zalando": TargetConfig(
        slug="zalando",
        name="Zalando",
        base_url="https://www.zalando.fr",
        protection=TargetProtection.PERIMETERX,
        allowed_methods=[
            ScrapingMethod.HTTP_RESIDENTIAL,
            ScrapingMethod.BROWSER_RESIDENTIAL,
        ],
        requires_residential=True,
        requests_per_second=0.5,
        enabled=False,  # Désactivé Phase 1
        disabled_reason="Requires residential proxies (PerimeterX protection)",
    ),
    "nike": TargetConfig(
        slug="nike",
        name="Nike",
        base_url="https://www.nike.com/fr",
        protection=TargetProtection.AKAMAI,
        allowed_methods=[
            ScrapingMethod.HTTP_RESIDENTIAL,
            ScrapingMethod.BROWSER_RESIDENTIAL,
        ],
        requires_residential=True,
        requests_per_second=0.3,
        enabled=False,  # Désactivé Phase 1
        disabled_reason="Requires residential proxies (Akamai protection)",
    ),
}


def get_target_config(slug: str) -> TargetConfig:
    """Retourne la config d'une cible."""
    return TARGET_CONFIGS.get(slug, TargetConfig(
        slug=slug,
        name=slug.capitalize(),
        base_url=f"https://www.{slug}.com",
        enabled=False,
        disabled_reason="Unknown target",
    ))


def get_enabled_targets() -> List[str]:
    """Retourne la liste des cibles activées."""
    return [slug for slug, config in TARGET_CONFIGS.items() if config.enabled]


def get_disabled_targets() -> List[Tuple[str, str]]:
    """Retourne les cibles désactivées avec leur raison."""
    return [
        (slug, config.disabled_reason or "Unknown")
        for slug, config in TARGET_CONFIGS.items()
        if not config.enabled
    ]


def is_target_available(slug: str) -> Tuple[bool, Optional[str]]:
    """
    Vérifie si une cible est disponible pour le scraping.
    
    Returns:
        (is_available, reason_if_not)
    """
    config = get_target_config(slug)
    
    if not config.enabled:
        return False, config.disabled_reason
    
    if config.requires_residential and not has_residential_proxies():
        return False, "Requires residential proxies (not configured)"
    
    return True, None


# =============================================================================
# METRICS & DECISION ENGINE
# =============================================================================

@dataclass
class RequestMetrics:
    target: str
    method: ScrapingMethod
    url: str
    timestamp: datetime
    duration_ms: float
    status_code: Optional[int]
    error_type: ErrorType
    response_size: int = 0
    proxy_used: bool = False


@dataclass
class TargetStats:
    target: str
    current_method: ScrapingMethod
    total_requests: int = 0
    success_count: int = 0
    error_counts: Dict[str, int] = field(default_factory=dict)
    last_success: Optional[datetime] = None
    last_error: Optional[datetime] = None
    last_error_type: Optional[ErrorType] = None
    consecutive_failures: int = 0
    escalation_count: int = 0
    cooldown_until: Optional[datetime] = None
    recent_requests: List[RequestMetrics] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.success_count / self.total_requests
    
    @property
    def recent_success_rate(self) -> float:
        if not self.recent_requests:
            return 1.0
        successes = sum(1 for r in self.recent_requests if r.error_type == ErrorType.SUCCESS)
        return successes / len(self.recent_requests)
    
    @property
    def is_cooling_down(self) -> bool:
        if self.cooldown_until is None:
            return False
        return datetime.utcnow() < self.cooldown_until
    
    def add_request(self, metrics: RequestMetrics) -> None:
        self.total_requests += 1
        
        if metrics.error_type == ErrorType.SUCCESS:
            self.success_count += 1
            self.last_success = metrics.timestamp
            self.consecutive_failures = 0
        else:
            self.last_error = metrics.timestamp
            self.last_error_type = metrics.error_type
            self.consecutive_failures += 1
            error_key = metrics.error_type.value
            self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1
        
        self.recent_requests.append(metrics)
        cutoff = datetime.utcnow() - timedelta(minutes=FALLBACK_THRESHOLDS["window_minutes"])
        self.recent_requests = [r for r in self.recent_requests if r.timestamp > cutoff]


class DecisionEngine:
    def __init__(self):
        self._stats: Dict[str, TargetStats] = {}
        self._lock = Lock()
    
    def _get_stats(self, target: str) -> TargetStats:
        if target not in self._stats:
            config = get_target_config(target)
            initial_method = config.allowed_methods[0] if config.allowed_methods else ScrapingMethod.HTTP_DIRECT
            self._stats[target] = TargetStats(target=target, current_method=initial_method)
        return self._stats[target]
    
    def record_result(
        self,
        target: str,
        method: ScrapingMethod,
        url: str,
        duration_ms: float,
        status_code: Optional[int],
        error_type: ErrorType,
        response_size: int = 0,
        proxy_used: bool = False,
    ) -> None:
        with self._lock:
            stats = self._get_stats(target)
            metrics = RequestMetrics(
                target=target,
                method=method,
                url=url,
                timestamp=datetime.utcnow(),
                duration_ms=duration_ms,
                status_code=status_code,
                error_type=error_type,
                response_size=response_size,
                proxy_used=proxy_used,
            )
            stats.add_request(metrics)
            stats.current_method = method
            
            if error_type != ErrorType.SUCCESS:
                logger.warning(
                    "Request failed",
                    target=target,
                    method=method.value,
                    error=error_type.value,
                    status_code=status_code,
                    consecutive_failures=stats.consecutive_failures,
                )
    
    def should_escalate(self, target: str) -> Tuple[bool, Optional[ScrapingMethod]]:
        with self._lock:
            stats = self._get_stats(target)
            config = get_target_config(target)
            
            recent_403 = sum(1 for r in stats.recent_requests if r.error_type == ErrorType.HTTP_403)
            recent_timeout = sum(1 for r in stats.recent_requests if r.error_type == ErrorType.TIMEOUT)
            
            should_escalate = False
            reason = ""
            
            if recent_403 >= FALLBACK_THRESHOLDS["403_count_before_escalate"]:
                should_escalate = True
                reason = f"403 count ({recent_403}) >= threshold"
            elif recent_timeout >= FALLBACK_THRESHOLDS["timeout_count_before_escalate"]:
                should_escalate = True
                reason = f"Timeout count ({recent_timeout}) >= threshold"
            elif len(stats.recent_requests) >= 5 and stats.recent_success_rate < FALLBACK_THRESHOLDS["success_rate_min"]:
                should_escalate = True
                reason = f"Success rate ({stats.recent_success_rate:.1%}) < threshold"
            
            if not should_escalate:
                return False, None
            
            current_idx = -1
            for i, m in enumerate(config.allowed_methods):
                if m == stats.current_method:
                    current_idx = i
                    break
            
            if current_idx < 0 or current_idx >= len(config.allowed_methods) - 1:
                logger.warning(
                    "Cannot escalate further",
                    target=target,
                    current_method=stats.current_method.value,
                    reason=reason,
                )
                return False, None
            
            new_method = config.allowed_methods[current_idx + 1]
            
            # Ne pas escalader vers résidentiel si non configuré
            if new_method in [ScrapingMethod.HTTP_RESIDENTIAL, ScrapingMethod.BROWSER_RESIDENTIAL]:
                if not has_residential_proxies():
                    logger.warning(
                        "Cannot escalate to residential - not configured",
                        target=target,
                    )
                    return False, None
            
            stats.escalation_count += 1
            stats.consecutive_failures = 0
            
            logger.info(
                "Escalating scraping method",
                target=target,
                from_method=stats.current_method.value,
                to_method=new_method.value,
                reason=reason,
            )
            
            return True, new_method
    
    def should_cooldown(self, target: str) -> Tuple[bool, int]:
        with self._lock:
            stats = self._get_stats(target)
            
            if stats.is_cooling_down:
                remaining = (stats.cooldown_until - datetime.utcnow()).total_seconds()
                return True, int(remaining)
            
            recent_429 = sum(1 for r in stats.recent_requests if r.error_type == ErrorType.HTTP_429)
            
            if recent_429 > 0:
                cooldown_sec = FALLBACK_THRESHOLDS["429_backoff_seconds"] * recent_429
                stats.cooldown_until = datetime.utcnow() + timedelta(seconds=cooldown_sec)
                logger.info(
                    "Entering cooldown",
                    target=target,
                    seconds=cooldown_sec,
                )
                return True, cooldown_sec
            
            return False, 0
    
    def get_current_method(self, target: str) -> ScrapingMethod:
        with self._lock:
            return self._get_stats(target).current_method
    
    def reset_target(self, target: str) -> None:
        with self._lock:
            config = get_target_config(target)
            initial_method = config.allowed_methods[0] if config.allowed_methods else ScrapingMethod.HTTP_DIRECT
            self._stats[target] = TargetStats(target=target, current_method=initial_method)
            logger.info("Target stats reset", target=target)
    
    def get_all_stats(self) -> Dict[str, Dict]:
        with self._lock:
            return {
                target: {
                    "current_method": stats.current_method.value,
                    "total_requests": stats.total_requests,
                    "success_rate": round(stats.success_rate * 100, 1),
                    "recent_success_rate": round(stats.recent_success_rate * 100, 1),
                    "consecutive_failures": stats.consecutive_failures,
                    "escalation_count": stats.escalation_count,
                    "is_cooling_down": stats.is_cooling_down,
                    "error_counts": stats.error_counts,
                }
                for target, stats in self._stats.items()
            }


_decision_engine = DecisionEngine()


def get_decision_engine() -> DecisionEngine:
    return _decision_engine


# =============================================================================
# SCRAPER FACTORY
# =============================================================================

class ScraperFactory:
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    @classmethod
    def get_headers(cls, target: str) -> Dict[str, str]:
        config = get_target_config(target)
        headers = {
            "User-Agent": random.choice(cls.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        headers.update(config.custom_headers)
        return headers
    
    @classmethod
    def create_http_scraper(
        cls,
        target: str,
        method: ScrapingMethod,
    ) -> Tuple[cloudscraper.CloudScraper, Optional[Dict[str, str]]]:
        scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "mobile": False,
            }
        )
        
        scraper.headers.update(cls.get_headers(target))
        
        proxies = None
        if method == ScrapingMethod.HTTP_DATACENTER:
            proxies = get_proxy("datacenter")
        elif method == ScrapingMethod.HTTP_RESIDENTIAL:
            proxies = get_proxy("residential")
        
        if proxies:
            scraper.proxies = proxies
        
        return scraper, proxies


# =============================================================================
# ORCHESTRATOR
# =============================================================================

class ScrapingOrchestrator:
    def __init__(self):
        self.engine = get_decision_engine()
    
    def fetch(
        self,
        target: str,
        url: str,
        timeout: int = 30,
        retry: bool = True,
    ) -> Tuple[Optional[str], ErrorType, Dict]:
        config = get_target_config(target)
        
        # Vérifier si la cible est disponible
        is_available, reason = is_target_available(target)
        if not is_available:
            return None, ErrorType.BLOCKED, {"reason": reason}
        
        # Vérifier cooldown
        in_cooldown, cooldown_sec = self.engine.should_cooldown(target)
        if in_cooldown:
            logger.debug(f"Target {target} in cooldown for {cooldown_sec}s")
            return None, ErrorType.HTTP_429, {"cooldown_seconds": cooldown_sec}
        
        # Obtenir méthode actuelle
        method = self.engine.get_current_method(target)
        
        # Vérifier si escalade nécessaire
        should_esc, new_method = self.engine.should_escalate(target)
        if should_esc and new_method:
            method = new_method
        
        # Vérifier que la méthode est autorisée
        if method not in config.allowed_methods:
            method = config.allowed_methods[0] if config.allowed_methods else ScrapingMethod.HTTP_DIRECT
        
        # Exécuter selon méthode
        if method == ScrapingMethod.BROWSER_RESIDENTIAL:
            return None, ErrorType.BLOCKED, {"reason": "Browser method not implemented yet"}
        
        return self._fetch_http(target, url, method, timeout, config)
    
    def _fetch_http(
        self,
        target: str,
        url: str,
        method: ScrapingMethod,
        timeout: int,
        config: TargetConfig,
    ) -> Tuple[Optional[str], ErrorType, Dict]:
        scraper, proxies = ScraperFactory.create_http_scraper(target, method)
        
        start_time = time.perf_counter()
        status_code = None
        error_type = ErrorType.SUCCESS
        content = None
        metadata = {
            "method": method.value,
            "proxy_used": proxies is not None,
        }
        
        try:
            resp = scraper.get(url, timeout=timeout)
            status_code = resp.status_code
            duration_ms = (time.perf_counter() - start_time) * 1000
            
            metadata["status_code"] = status_code
            metadata["duration_ms"] = round(duration_ms, 2)
            metadata["response_size"] = len(resp.content)
            
            if status_code == 200:
                content = resp.text
                error_type = ErrorType.SUCCESS
            elif status_code == 403:
                error_type = ErrorType.HTTP_403
            elif status_code == 429:
                error_type = ErrorType.HTTP_429
            elif status_code == 400:
                error_type = ErrorType.HTTP_400
            elif status_code >= 500:
                error_type = ErrorType.HTTP_5XX
            else:
                error_type = ErrorType.BLOCKED
                
        except requests.exceptions.Timeout:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_type = ErrorType.TIMEOUT
            metadata["duration_ms"] = round(duration_ms, 2)
        except requests.exceptions.RequestException as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_type = ErrorType.NETWORK
            metadata["duration_ms"] = round(duration_ms, 2)
            metadata["error"] = str(e)
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            error_type = ErrorType.NETWORK
            metadata["duration_ms"] = round(duration_ms, 2)
            metadata["error"] = str(e)
            logger.error(f"Unexpected error fetching {url}: {e}")
        
        self.engine.record_result(
            target=target,
            method=method,
            url=url,
            duration_ms=metadata.get("duration_ms", 0),
            status_code=status_code,
            error_type=error_type,
            response_size=metadata.get("response_size", 0),
            proxy_used=proxies is not None,
        )
        
        return content, error_type, metadata
    
    def get_stats(self) -> Dict:
        return {
            "targets": self.engine.get_all_stats(),
            "proxy_pool": {
                "datacenter": len(_proxy_pool.datacenter),
                "residential": len(_proxy_pool.residential),
                "has_residential": has_residential_proxies(),
            },
            "enabled_targets": get_enabled_targets(),
            "disabled_targets": dict(get_disabled_targets()),
        }


_orchestrator: Optional[ScrapingOrchestrator] = None


def get_orchestrator() -> ScrapingOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ScrapingOrchestrator()
    return _orchestrator
