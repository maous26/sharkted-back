"""
Proxy Service - Gestion intelligente des proxies pour le scraping.

Niveaux de proxy:
- NONE: Pas de proxy (connexion directe)
- LOW: Proxies datacenter gratuits (rotation basique)
- MEDIUM: Proxies datacenter premium (meilleure fiabilité)
- HIGH: Proxies résidentiels (pour sites avec protection avancée)

Le service:
1. Maintient un pool de proxies par niveau
2. Teste et valide les proxies automatiquement
3. Choisit le proxy optimal selon la source et les échecs précédents
4. Escalade automatiquement si besoin
"""
import random
import time
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List, Tuple
from threading import Lock
import cloudscraper

from app.core.logging import get_logger

logger = get_logger(__name__)


class ProxyLevel(str, Enum):
    NONE = "none"
    LOW = "low"          # Proxies gratuits/datacenter basiques
    MEDIUM = "medium"    # Proxies datacenter premium
    HIGH = "high"        # Proxies résidentiels (non implémenté - à acheter)


@dataclass
class ProxyInfo:
    """Information sur un proxy."""
    url: str
    level: ProxyLevel
    country: str = "FR"
    last_used: Optional[datetime] = None
    last_success: Optional[datetime] = None
    success_count: int = 0
    failure_count: int = 0
    avg_response_time_ms: float = 0
    is_working: bool = True
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.5
    
    def to_requests_format(self) -> Dict[str, str]:
        return {"http": self.url, "https": self.url}


@dataclass
class SourceProxyConfig:
    """Configuration proxy spécifique à une source."""
    min_level: ProxyLevel = ProxyLevel.NONE
    max_level: ProxyLevel = ProxyLevel.MEDIUM
    current_level: ProxyLevel = ProxyLevel.NONE
    failures_before_escalate: int = 3
    consecutive_failures: int = 0
    last_escalation: Optional[datetime] = None


class ProxyPool:
    """
    Pool de proxies avec gestion intelligente.
    """
    
    # Intervalle minimum entre deux utilisations du même proxy (secondes)
    MIN_REUSE_INTERVAL = 30
    
    # Nombre max d'échecs avant de marquer un proxy comme mort
    MAX_FAILURES = 5
    
    def __init__(self):
        self._proxies: Dict[ProxyLevel, List[ProxyInfo]] = {
            ProxyLevel.NONE: [],
            ProxyLevel.LOW: [],
            ProxyLevel.MEDIUM: [],
            ProxyLevel.HIGH: [],
        }
        self._source_configs: Dict[str, SourceProxyConfig] = {}
        self._lock = Lock()
        self._initialized = False
    
    def initialize(self):
        """Initialise le pool avec les proxies disponibles."""
        if self._initialized:
            return
            
        with self._lock:
            # Ajouter des proxies LOW (datacenter gratuits)
            # Ces proxies sont testés et ajoutés dynamiquement
            self._add_free_proxies()
            self._initialized = True
            logger.info(
                "Proxy pool initialized",
                low_count=len(self._proxies[ProxyLevel.LOW]),
                medium_count=len(self._proxies[ProxyLevel.MEDIUM]),
            )
    
    def _add_free_proxies(self):
        """Ajoute des proxies gratuits au pool."""
        # Liste de proxies datacenter publics (à tester)
        # En production, utiliser une API de proxy ou un service
        # TODO: Intégrer avec un service de proxy (ex: ScraperAPI, Smartproxy)
        pass
    
    def add_proxy(self, url: str, level: ProxyLevel, country: str = "FR"):
        """Ajoute un proxy au pool."""
        with self._lock:
            proxy = ProxyInfo(url=url, level=level, country=country)
            self._proxies[level].append(proxy)
            logger.info(f"Proxy added", url=url, level=level.value)
    
    def get_proxy(self, source: str, preferred_level: Optional[ProxyLevel] = None) -> Optional[ProxyInfo]:
        """
        Obtient le meilleur proxy disponible pour une source.
        
        Args:
            source: Nom de la source (courir, footlocker, etc.)
            preferred_level: Niveau préféré (sinon utilise la config source)
        
        Returns:
            ProxyInfo ou None si pas de proxy nécessaire/disponible
        """
        with self._lock:
            config = self._get_source_config(source)
            level = preferred_level or config.current_level
            
            # Si NONE, pas besoin de proxy
            if level == ProxyLevel.NONE:
                return None
            
            # Chercher un proxy disponible au niveau demandé
            proxy = self._find_available_proxy(level)
            
            # Si pas trouvé, essayer niveau inférieur
            if not proxy and level != ProxyLevel.LOW:
                lower_levels = [ProxyLevel.MEDIUM, ProxyLevel.LOW]
                for lower in lower_levels:
                    if lower.value < level.value:
                        proxy = self._find_available_proxy(lower)
                        if proxy:
                            break
            
            if proxy:
                proxy.last_used = datetime.utcnow()
                
            return proxy
    
    def _find_available_proxy(self, level: ProxyLevel) -> Optional[ProxyInfo]:
        """Trouve un proxy disponible au niveau spécifié."""
        proxies = [p for p in self._proxies[level] if p.is_working]
        
        if not proxies:
            return None
        
        # Filtrer ceux utilisés récemment
        now = datetime.utcnow()
        available = [
            p for p in proxies
            if not p.last_used or (now - p.last_used).total_seconds() > self.MIN_REUSE_INTERVAL
        ]
        
        if not available:
            # Si tous récemment utilisés, prendre le moins récent
            available = sorted(proxies, key=lambda p: p.last_used or datetime.min)
        
        # Choisir le meilleur (taux de succès + temps de réponse)
        return max(available, key=lambda p: (p.success_rate, -p.avg_response_time_ms))
    
    def _get_source_config(self, source: str) -> SourceProxyConfig:
        """Obtient ou crée la config proxy pour une source."""
        if source not in self._source_configs:
            # Config par défaut selon la source
            self._source_configs[source] = self._default_config_for_source(source)
        return self._source_configs[source]
    
    def _default_config_for_source(self, source: str) -> SourceProxyConfig:
        """
        Retourne la config proxy par défaut pour une source.
        
        Classification des sources par niveau de protection anti-bot:
        
        EASY (NONE -> MEDIUM): Sites accessibles sans proxy, escalade possible
        - courir: Pas de protection forte
        - jdsports: Protection basique
        - size: Protection basique (JD Group)
        
        HARD (HIGH requis): Sites avec protection avancée (Akamai, PerimeterX, etc.)
        - footlocker: HTTP 400 même avec cloudscraper - Akamai
        - adidas: Akamai 403 - protection très forte
        - zalando: PerimeterX - protection forte
        - snipes: SPA + protection
        - ralphlauren: 307 redirect + protection
        """
        # Sources qui fonctionnent sans proxy (escalade possible)
        easy_sources = ["courir", "jdsports", "size"]
        
        # Sources nécessitant des proxies résidentiels obligatoirement
        hard_sources = ["footlocker", "adidas", "zalando", "snipes", "ralphlauren", "galerieslafayette", "printemps"]
        
        if source in easy_sources:
            return SourceProxyConfig(
                min_level=ProxyLevel.NONE,
                max_level=ProxyLevel.MEDIUM,
                current_level=ProxyLevel.NONE,
                failures_before_escalate=3,
            )
        elif source in hard_sources:
            return SourceProxyConfig(
                min_level=ProxyLevel.HIGH,
                max_level=ProxyLevel.HIGH,
                current_level=ProxyLevel.HIGH,
                failures_before_escalate=2,
            )
        else:
            # Par défaut: commencer sans proxy, escalader si nécessaire
            return SourceProxyConfig(
                min_level=ProxyLevel.NONE,
                max_level=ProxyLevel.MEDIUM,
                current_level=ProxyLevel.NONE,
            )
    
    def record_success(self, source: str, proxy: Optional[ProxyInfo], response_time_ms: float):
        """Enregistre un succès pour un proxy."""
        with self._lock:
            config = self._get_source_config(source)
            config.consecutive_failures = 0
            
            if proxy:
                proxy.success_count += 1
                proxy.last_success = datetime.utcnow()
                # Moyenne mobile du temps de réponse
                proxy.avg_response_time_ms = (
                    proxy.avg_response_time_ms * 0.7 + response_time_ms * 0.3
                )
    
    def record_failure(self, source: str, proxy: Optional[ProxyInfo], error_type: str):
        """Enregistre un échec et gère l'escalade."""
        with self._lock:
            config = self._get_source_config(source)
            config.consecutive_failures += 1
            
            if proxy:
                proxy.failure_count += 1
                if proxy.failure_count >= self.MAX_FAILURES:
                    proxy.is_working = False
                    logger.warning(f"Proxy marked as dead", url=proxy.url)
            
            # Escalade si trop d'échecs
            if config.consecutive_failures >= config.failures_before_escalate:
                self._escalate(source, config)
    
    def _escalate(self, source: str, config: SourceProxyConfig):
        """Escalade vers un niveau de proxy supérieur."""
        level_order = [ProxyLevel.NONE, ProxyLevel.LOW, ProxyLevel.MEDIUM, ProxyLevel.HIGH]
        current_idx = level_order.index(config.current_level)
        max_idx = level_order.index(config.max_level)
        
        if current_idx < max_idx:
            new_level = level_order[current_idx + 1]
            config.current_level = new_level
            config.consecutive_failures = 0
            config.last_escalation = datetime.utcnow()
            logger.info(
                f"Proxy level escalated",
                source=source,
                new_level=new_level.value,
            )
        else:
            logger.warning(
                f"Cannot escalate further - max level reached",
                source=source,
                max_level=config.max_level.value,
            )
    
    def get_source_level(self, source: str) -> ProxyLevel:
        """Retourne le niveau de proxy actuel pour une source."""
        with self._lock:
            return self._get_source_config(source).current_level
    
    def reset_source(self, source: str):
        """Reset la config d'une source à son niveau minimum."""
        with self._lock:
            config = self._get_source_config(source)
            config.current_level = config.min_level
            config.consecutive_failures = 0
            logger.info(f"Source proxy config reset", source=source)
    
    def get_stats(self) -> Dict:
        """Retourne les statistiques du pool."""
        with self._lock:
            return {
                "levels": {
                    level.value: {
                        "total": len(proxies),
                        "working": len([p for p in proxies if p.is_working]),
                    }
                    for level, proxies in self._proxies.items()
                },
                "sources": {
                    source: {
                        "current_level": config.current_level.value,
                        "min_level": config.min_level.value,
                        "max_level": config.max_level.value,
                        "consecutive_failures": config.consecutive_failures,
                        "needs_residential": config.min_level == ProxyLevel.HIGH,
                    }
                    for source, config in self._source_configs.items()
                },
            }


# Singleton global
_proxy_pool = ProxyPool()


def get_proxy_pool() -> ProxyPool:
    """Retourne le pool de proxies global."""
    if not _proxy_pool._initialized:
        _proxy_pool.initialize()
    return _proxy_pool


def get_proxy_for_source(source: str) -> Optional[ProxyInfo]:
    """Raccourci pour obtenir un proxy pour une source."""
    return get_proxy_pool().get_proxy(source)


def record_proxy_success(source: str, proxy: Optional[ProxyInfo], response_time_ms: float):
    """Enregistre un succès."""
    get_proxy_pool().record_success(source, proxy, response_time_ms)


def record_proxy_failure(source: str, proxy: Optional[ProxyInfo], error_type: str):
    """Enregistre un échec."""
    get_proxy_pool().record_failure(source, proxy, error_type)


def get_scraper_with_proxy(source: str) -> Tuple[cloudscraper.CloudScraper, Optional[ProxyInfo]]:
    """
    Crée un scraper configuré avec le bon proxy pour la source.
    
    Returns:
        Tuple (scraper, proxy_info)
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    
    proxy = get_proxy_for_source(source)
    
    if proxy:
        scraper.proxies = proxy.to_requests_format()
        logger.debug(f"Using proxy for {source}", proxy_level=proxy.level.value)
    else:
        # Vérifier si la source nécessite un proxy HIGH
        pool = get_proxy_pool()
        required_level = pool.get_source_level(source)
        if required_level == ProxyLevel.HIGH:
            logger.warning(
                f"Source {source} requires HIGH (residential) proxy but none available",
                source=source,
            )
    
    return scraper, proxy
