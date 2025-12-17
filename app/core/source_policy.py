"""
Source Policy - Stratégie de collecte hybride par source.

Modes (ordre d'escalade):
- DIRECT: cloudscraper rapide, pas de warmup
- DIRECT_SLOW: cadence lente, warmup (home→category→product), cookies/consent, cache agressif
- PROXY: rotation via pool datacenter/VPS
- BROWSER: Playwright pour SPA/JS
- BLOCKED: source désactivée temporairement

Logique d'escalade:
1. DIRECT (rapide)
2. Si 403/429 → DIRECT_SLOW (warmup + cadence lente)
3. Si toujours 403 → PROXY (si autorisé)
4. Si SPA/JS → BROWSER (si autorisé)
5. Sinon → BLOCKED (temporaire)
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List
from threading import Lock
import random
import time

from app.core.logging import get_logger

logger = get_logger(__name__)


class CollectMode(str, Enum):
    DIRECT = "direct"
    DIRECT_SLOW = "direct_slow"  # Nouveau mode avec warmup
    PROXY = "proxy"
    BROWSER = "browser"
    BLOCKED = "blocked"
    WEB_UNLOCKER = "web_unlocker"  # Premium - BrightData Web Unlocker


@dataclass
class WarmupConfig:
    """Configuration du warmup pour DIRECT_SLOW."""
    # URLs de warmup (visitées avant le produit)
    homepage: Optional[str] = None
    category_patterns: List[str] = field(default_factory=list)

    # Délais entre requêtes (secondes)
    delay_min: float = 2.0
    delay_max: float = 5.0

    # Accepter cookies/consent
    accept_cookies: bool = True

    # Cache agressif
    cache_homepage_sec: int = 3600  # 1h
    cache_category_sec: int = 1800  # 30min


@dataclass
class ProxyConfig:
    """
    Configuration proxy pour le mode PROXY.

    Prêt pour intégration future avec:
    - Smartproxy, Bright Data, IPRoyal, etc.
    - Proxies datacenter ou résidentiels

    NON ACTIVÉ pour l'instant - préparé pour usage futur.
    """
    # Provider (smartproxy, brightdata, iproyal, custom)
    provider: str = "none"

    # Endpoint du proxy
    endpoint: Optional[str] = None  # ex: "gate.smartproxy.com:7000"

    # Authentification
    username: Optional[str] = None
    password: Optional[str] = None

    # Options
    country: str = "FR"  # Rotation géographique
    session_type: str = "rotating"  # rotating ou sticky

    # Enabled flag
    enabled: bool = False  # Désactivé par défaut

    def to_requests_format(self) -> Optional[Dict[str, str]]:
        """Convertit en format requests/cloudscraper."""
        if not self.enabled or not self.endpoint:
            return None

        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"

        proxy_url = f"http://{auth}{self.endpoint}"
        return {
            "http": proxy_url,
            "https": proxy_url,
        }


# Configuration proxy globale (désactivée par défaut)
PROXY_CONFIG = ProxyConfig(
    provider="none",
    enabled=False,
)


@dataclass
class SourcePolicy:
    """Configuration de collecte pour une source."""
    mode: CollectMode = CollectMode.DIRECT
    max_retries: int = 2
    base_interval_sec: int = 60  # Intervalle entre collectes
    backoff_multiplier: float = 2.0
    max_backoff_sec: int = 300
    use_cache: bool = True
    cache_ttl_sec: int = 300  # 5 min
    allow_slow: bool = True  # Peut escalader vers DIRECT_SLOW
    allow_proxy: bool = False  # Peut escalader vers PROXY
    allow_browser: bool = False  # Peut escalader vers BROWSER
    enabled: bool = True
    reason: Optional[str] = None  # Raison si disabled/blocked
    plan_required: str = "free"  # "free", "basic", "premium"

    # Config warmup pour DIRECT_SLOW
    warmup: Optional[WarmupConfig] = None


# Configuration par source
SOURCE_POLICIES: Dict[str, SourcePolicy] = {
    "courir": SourcePolicy(
        mode=CollectMode.DIRECT,
        base_interval_sec=60,
        allow_slow=True,
        allow_proxy=True,
        enabled=True,
        warmup=WarmupConfig(
            homepage="https://www.courir.com/fr/",
            category_patterns=["/fr/c/homme/", "/fr/c/femme/"],
        ),
    ),
    "footlocker": SourcePolicy(
        mode=CollectMode.DIRECT,
        base_interval_sec=60,
        allow_slow=True,
        allow_proxy=True,
        enabled=True,
        warmup=WarmupConfig(
            homepage="https://www.footlocker.fr/",
            category_patterns=["/fr/category/hommes/chaussures/"],
        ),
    ),
    "size": SourcePolicy(
        mode=CollectMode.DIRECT,
        base_interval_sec=60,
        allow_slow=True,
        allow_proxy=True,
        enabled=True,
        warmup=WarmupConfig(
            homepage="https://www.size.co.uk/",
            category_patterns=["/mens/footwear/"],
        ),
    ),
    "jdsports": SourcePolicy(
        mode=CollectMode.DIRECT,
        base_interval_sec=60,
        allow_slow=True,
        allow_proxy=True,
        enabled=True,
        warmup=WarmupConfig(
            homepage="https://www.jdsports.fr/",
            category_patterns=["/homme/chaussures-homme/"],
        ),
    ),
    "kith": SourcePolicy(
        # Utilise son propre job jobs_kith.py, pas le scraping général
        mode=CollectMode.DIRECT,
        base_interval_sec=120,
        allow_slow=True,
        allow_proxy=False,
        enabled=True,
        reason="Shopify JSON API - pas de protection",
    ),
    # === SOURCES GRATUITES SUPPLÉMENTAIRES ===
    "footpatrol": SourcePolicy(
        mode=CollectMode.DIRECT,
        base_interval_sec=60,
        allow_slow=True,
        allow_proxy=True,
        enabled=True,
        reason="Shopify JSON API - fonctionne bien",
        warmup=WarmupConfig(
            homepage="https://www.footpatrol.com/",
            category_patterns=["/sale/", "/mens/footwear/"],
        ),
    ),
    "snipes": SourcePolicy(
        mode=CollectMode.BROWSER,
        allow_browser=True,
        enabled=False,
        reason="SPA - Playwright requis (non implémenté)",
    ),
    # === SOURCES SPA (désactivées - nécessitent Playwright) ===
    "galerieslafayette": SourcePolicy(
        mode=CollectMode.BROWSER,
        allow_browser=True,
        enabled=False,
        plan_required="premium",
        reason="SPA - nécessite Playwright pour le rendu JavaScript",
    ),
    "asos": SourcePolicy(
        mode=CollectMode.WEB_UNLOCKER,
        enabled=True,
        plan_required="premium",
        reason="Web Unlocker requis",
    ),
    "laredoute": SourcePolicy(
        mode=CollectMode.WEB_UNLOCKER,
        enabled=True,
        plan_required="premium",
        reason="Web Unlocker requis",
    ),
    "printemps": SourcePolicy(
        mode=CollectMode.BROWSER,
        allow_browser=True,
        enabled=True,
        plan_required="premium",
        reason="SPA - nécessite Playwright pour le rendu JavaScript",
    ),
    "sns": SourcePolicy(
        mode=CollectMode.BROWSER,
        allow_browser=True,
        enabled=False,
        plan_required="premium",
        reason="SPA - nécessite Playwright pour le rendu JavaScript",
    ),
    "bstn": SourcePolicy(
        mode=CollectMode.BROWSER,
        allow_browser=True,
        enabled=False,
        reason="SPA - nécessite Playwright pour le rendu JavaScript",
    ),
    # === SOURCES BLOQUÉES ===
    "ralphlauren": SourcePolicy(
        mode=CollectMode.BLOCKED,
        enabled=False,
        reason="307 redirect + protection - nécessite proxy résidentiel",
    ),
    "adidas": SourcePolicy(
        mode=CollectMode.BLOCKED,
        enabled=False,
        reason="Akamai 403 - protection trop forte même avec DIRECT_SLOW",
    ),
}

# Default policy pour sources non configurées
DEFAULT_POLICY = SourcePolicy(
    mode=CollectMode.DIRECT,
    max_retries=2,
    base_interval_sec=120,
    allow_slow=True,
    allow_proxy=False,
    allow_browser=False,
    enabled=True,
)


def get_policy(source: str) -> SourcePolicy:
    """Retourne la policy pour une source."""
    return SOURCE_POLICIES.get(source, DEFAULT_POLICY)


def register_source(source: str, policy: SourcePolicy) -> None:
    """Enregistre une nouvelle source avec sa policy."""
    SOURCE_POLICIES[source] = policy
    logger.info(f"Source registered", source=source, mode=policy.mode.value)


# =============================================================================
# WARMUP HELPER - Stratégie de warmup pour DIRECT_SLOW
# =============================================================================

class WarmupSession:
    """
    Gère le warmup d'une session avant collecte.
    Simule un parcours utilisateur: home → category → product
    """

    def __init__(self, scraper, source: str, config: WarmupConfig):
        self.scraper = scraper
        self.source = source
        self.config = config
        self._warmed_up = False
        self._cookies_accepted = False

    def _random_delay(self) -> None:
        """Pause aléatoire entre requêtes."""
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        time.sleep(delay)

    def _accept_cookies(self, html: str) -> None:
        """Détecte et simule l'acceptation des cookies (basique)."""
        self._cookies_accepted = True

    def warmup(self) -> bool:
        """
        Exécute la séquence de warmup.

        Returns:
            True si warmup réussi, False sinon.
        """
        if self._warmed_up:
            return True

        try:
            # 1. Visiter la homepage
            if self.config.homepage:
                logger.debug(f"Warmup: visiting homepage", source=self.source)
                resp = self.scraper.get(self.config.homepage, timeout=20)

                if resp.status_code == 403:
                    logger.warning(f"Warmup blocked on homepage", source=self.source)
                    return False

                if resp.status_code == 200:
                    self._accept_cookies(resp.text)

                self._random_delay()

            # 2. Visiter une catégorie (optionnel)
            if self.config.category_patterns:
                cat_path = random.choice(self.config.category_patterns)
                if self.config.homepage:
                    base = self.config.homepage.rstrip('/')
                    cat_url = base + cat_path

                    logger.debug(f"Warmup: visiting category", source=self.source, url=cat_url)
                    resp = self.scraper.get(cat_url, timeout=20)

                    if resp.status_code == 403:
                        logger.debug(f"Warmup: category blocked, continuing", source=self.source)

                    self._random_delay()

            self._warmed_up = True
            logger.info(f"Warmup completed", source=self.source)
            return True

        except Exception as e:
            logger.warning(f"Warmup failed: {e}", source=self.source, error_type=type(e).__name__)
            return False


def create_warmup_session(scraper, source: str) -> Optional[WarmupSession]:
    """Crée une session de warmup si la source le supporte."""
    policy = get_policy(source)
    if policy.warmup:
        return WarmupSession(scraper, source, policy.warmup)
    return None


# =============================================================================
# OUTCOME TRACKING - Métriques simples en mémoire
# =============================================================================

@dataclass
class SourceMetrics:
    """Métriques agrégées pour une source."""
    source: str
    total_attempts: int = 0
    total_success: int = 0
    total_failures: int = 0
    last_success_at: Optional[datetime] = None
    last_error_at: Optional[datetime] = None
    last_error_type: Optional[str] = None
    last_status_code: Optional[int] = None
    current_mode: CollectMode = CollectMode.DIRECT
    blocked_until: Optional[datetime] = None
    consecutive_failures: int = 0

    # Stats 24h (approximation simple)
    success_24h: int = 0
    failures_24h: int = 0

    @property
    def success_rate_24h(self) -> float:
        total = self.success_24h + self.failures_24h
        if total == 0:
            return 0.0
        return round(self.success_24h / total * 100, 1)

    @property
    def is_blocked(self) -> bool:
        if self.blocked_until is None:
            return False
        return datetime.utcnow() < self.blocked_until


class OutcomeTracker:
    """
    Tracker des outcomes par source.
    Thread-safe, stockage en mémoire (suffisant pour MVP).
    """

    FAILURES_BEFORE_ESCALATE = 3
    BLOCK_DURATION_MIN = 30

    def __init__(self):
        self._metrics: Dict[str, SourceMetrics] = {}
        self._lock = Lock()

    def _get_or_create(self, source: str) -> SourceMetrics:
        if source not in self._metrics:
            policy = get_policy(source)
            self._metrics[source] = SourceMetrics(
                source=source,
                current_mode=policy.mode,
            )
        return self._metrics[source]

    def record_outcome(
        self,
        source: str,
        mode: CollectMode,
        success: bool,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
        duration_ms: float = 0,
    ) -> None:
        """Enregistre le résultat d'une collecte."""
        with self._lock:
            m = self._get_or_create(source)
            m.total_attempts += 1
            m.current_mode = mode

            if success:
                m.total_success += 1
                m.success_24h += 1
                m.last_success_at = datetime.utcnow()
                m.consecutive_failures = 0
                if m.blocked_until:
                    m.blocked_until = None
                    logger.info(f"Source unblocked after success", source=source)
            else:
                m.total_failures += 1
                m.failures_24h += 1
                m.last_error_at = datetime.utcnow()
                m.last_error_type = error_type
                m.last_status_code = status_code
                m.consecutive_failures += 1

            logger.debug(
                "Outcome recorded",
                source=source,
                mode=mode.value,
                success=success,
                status_code=status_code,
                consecutive_failures=m.consecutive_failures,
            )

    def should_escalate(self, source: str, error_type: Optional[str] = None) -> Optional[CollectMode]:
        """Détermine si on doit escalader vers un mode supérieur."""
        with self._lock:
            m = self._get_or_create(source)
            policy = get_policy(source)

            if m.consecutive_failures < self.FAILURES_BEFORE_ESCALATE:
                return None

            current = m.current_mode

            if current == CollectMode.DIRECT and policy.allow_slow:
                logger.warning(
                    f"Escalating to DIRECT_SLOW",
                    source=source,
                    consecutive_failures=m.consecutive_failures,
                )
                m.current_mode = CollectMode.DIRECT_SLOW
                m.consecutive_failures = 0
                return CollectMode.DIRECT_SLOW

            if current == CollectMode.DIRECT_SLOW and policy.allow_proxy:
                logger.warning(
                    f"Escalating to PROXY",
                    source=source,
                    consecutive_failures=m.consecutive_failures,
                )
                m.current_mode = CollectMode.PROXY
                m.consecutive_failures = 0
                return CollectMode.PROXY

            if current == CollectMode.PROXY and policy.allow_browser:
                if error_type in ("DataExtractionError", "SPADetected"):
                    logger.warning(
                        f"Escalating to BROWSER",
                        source=source,
                        error_type=error_type,
                    )
                    m.current_mode = CollectMode.BROWSER
                    m.consecutive_failures = 0
                    return CollectMode.BROWSER

            if m.consecutive_failures >= self.FAILURES_BEFORE_ESCALATE * 2:
                logger.error(
                    f"Source blocked due to repeated failures",
                    source=source,
                    consecutive_failures=m.consecutive_failures,
                )
                m.current_mode = CollectMode.BLOCKED
                m.blocked_until = datetime.utcnow() + timedelta(minutes=self.BLOCK_DURATION_MIN)
                return CollectMode.BLOCKED

            return None

    def get_current_mode(self, source: str) -> CollectMode:
        """Retourne le mode actuel pour une source."""
        with self._lock:
            m = self._get_or_create(source)
            if m.is_blocked:
                return CollectMode.BLOCKED
            return m.current_mode

    def get_metrics(self, source: str) -> SourceMetrics:
        """Retourne les métriques pour une source."""
        with self._lock:
            return self._get_or_create(source)

    def get_all_metrics(self) -> Dict[str, SourceMetrics]:
        """Retourne les métriques de toutes les sources."""
        with self._lock:
            for source in SOURCE_POLICIES:
                self._get_or_create(source)
            return dict(self._metrics)

    def unblock(self, source: str) -> bool:
        """Débloque manuellement une source."""
        with self._lock:
            m = self._get_or_create(source)
            was_blocked = m.is_blocked or m.current_mode == CollectMode.BLOCKED
            m.blocked_until = None
            m.consecutive_failures = 0

            policy = get_policy(source)
            m.current_mode = policy.mode if policy.enabled else CollectMode.BLOCKED

            if was_blocked:
                logger.info(f"Source manually unblocked", source=source)

            return was_blocked

    def reset_24h_stats(self) -> None:
        """Reset les stats 24h (à appeler via cron/scheduler)."""
        with self._lock:
            for m in self._metrics.values():
                m.success_24h = 0
                m.failures_24h = 0


# Singleton global
_tracker = OutcomeTracker()


def record_outcome(
    source: str,
    mode: CollectMode,
    success: bool,
    status_code: Optional[int] = None,
    error_type: Optional[str] = None,
    duration_ms: float = 0,
) -> None:
    """Enregistre le résultat d'une collecte."""
    _tracker.record_outcome(source, mode, success, status_code, error_type, duration_ms)


def should_escalate(source: str, error_type: Optional[str] = None) -> Optional[CollectMode]:
    """Détermine si on doit escalader vers un mode supérieur."""
    return _tracker.should_escalate(source, error_type)


def get_current_mode(source: str) -> CollectMode:
    """Retourne le mode actuel pour une source."""
    return _tracker.get_current_mode(source)


def get_source_metrics(source: str) -> SourceMetrics:
    """Retourne les métriques pour une source."""
    return _tracker.get_metrics(source)


def get_all_source_metrics() -> Dict[str, SourceMetrics]:
    """Retourne les métriques de toutes les sources."""
    return _tracker.get_all_metrics()


def unblock_source(source: str) -> bool:
    """Débloque manuellement une source."""
    return _tracker.unblock(source)


# =============================================================================
# QUEUE PICKING - Choix de queue selon user/context
# =============================================================================

def pick_queue(user: Optional[dict], kind: str = "collect") -> str:
    """Choisit la queue appropriée selon l'utilisateur et le type de job."""
    is_premium = user.get("is_premium", False) if user else False

    if kind == "alert":
        return "high"

    if is_premium:
        if kind in ("collect", "refresh"):
            return "high"
        return "default"

    if kind == "batch":
        return "low"

    return "default"


# =============================================================================
# PROXY HELPERS - Fonctions utilitaires pour les proxies (usage futur)
# =============================================================================

def get_proxy_config() -> ProxyConfig:
    """Retourne la configuration proxy globale."""
    return PROXY_CONFIG


def is_proxy_enabled() -> bool:
    """Vérifie si les proxies sont activés."""
    return PROXY_CONFIG.enabled


def get_proxies_for_requests() -> Optional[Dict[str, str]]:
    """Retourne les proxies au format requests/cloudscraper."""
    return PROXY_CONFIG.to_requests_format()


def enable_proxy(
    provider: str,
    endpoint: str,
    username: str,
    password: str,
    country: str = "FR",
) -> None:
    """Active les proxies avec la configuration fournie."""
    global PROXY_CONFIG
    PROXY_CONFIG = ProxyConfig(
        provider=provider,
        endpoint=endpoint,
        username=username,
        password=password,
        country=country,
        enabled=True,
    )
    logger.info(
        f"Proxy enabled",
        provider=provider,
        endpoint=endpoint,
        country=country,
    )


def disable_proxy() -> None:
    """Désactive les proxies."""
    global PROXY_CONFIG
    PROXY_CONFIG = ProxyConfig(enabled=False)
    logger.info("Proxy disabled")
