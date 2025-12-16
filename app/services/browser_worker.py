"""
Browser Worker - Worker Playwright pour les sites avec protection avancée.

Ce worker utilise Playwright pour:
- Contourner les protections Akamai/PerimeterX
- Exécuter le JavaScript pour les SPAs
- Résoudre les challenges automatiquement

Architecture:
- Worker dédié pour isoler les ressources (CPU/RAM)
- Queue séparée (high) pour prioriser
- Pool de browsers avec réutilisation
"""
import asyncio
import random
import time
from typing import Optional, Dict, Tuple, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

from app.core.logging import get_logger
from app.services.scraping_orchestrator import (
    get_proxy,
    get_target_config,
    ErrorType,
)

logger = get_logger(__name__)

# Flag pour vérifier si Playwright est disponible
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.warning("Playwright not installed - browser scraping disabled")


# =============================================================================
# BROWSER POOL
# =============================================================================

@dataclass
class BrowserSession:
    """Session browser réutilisable."""
    browser: Any  # Browser
    context: Any  # BrowserContext
    page: Any     # Page
    created_at: float
    requests_count: int = 0
    max_requests: int = 50  # Recycler après N requêtes


class BrowserPool:
    """
    Pool de browsers Playwright avec réutilisation.
    
    Optimisations:
    - Réutilise les browsers pour éviter le coût de démarrage
    - Recycle après N requêtes pour éviter les fuites mémoire
    - Limite le nombre de browsers concurrents
    """
    
    MAX_BROWSERS = 3
    SESSION_MAX_AGE = 300  # 5 minutes
    
    def __init__(self):
        self._sessions: list[BrowserSession] = []
        self._playwright = None
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def initialize(self):
        """Initialise Playwright."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")
        
        if self._initialized:
            return
        
        self._playwright = await async_playwright().start()
        self._initialized = True
        logger.info("Browser pool initialized")
    
    async def get_session(self, proxy: Optional[Dict[str, str]] = None) -> BrowserSession:
        """
        Obtient une session browser du pool.
        
        Si aucune session disponible, en crée une nouvelle.
        """
        async with self._lock:
            if not self._initialized:
                await self.initialize()
            
            # Chercher une session existante valide
            now = time.time()
            for session in self._sessions:
                age = now - session.created_at
                if (session.requests_count < session.max_requests and 
                    age < self.SESSION_MAX_AGE):
                    session.requests_count += 1
                    return session
            
            # Nettoyer les vieilles sessions
            await self._cleanup_old_sessions()
            
            # Créer nouvelle session si possible
            if len(self._sessions) < self.MAX_BROWSERS:
                session = await self._create_session(proxy)
                self._sessions.append(session)
                return session
            
            # Recycler la plus vieille
            oldest = min(self._sessions, key=lambda s: s.created_at)
            await self._close_session(oldest)
            self._sessions.remove(oldest)
            
            session = await self._create_session(proxy)
            self._sessions.append(session)
            return session
    
    async def _create_session(self, proxy: Optional[Dict[str, str]] = None) -> BrowserSession:
        """Crée une nouvelle session browser."""
        launch_options = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }
        
        if proxy:
            # Extraire host:port du proxy URL
            proxy_url = proxy.get("http", "")
            if "@" in proxy_url:
                # Format: http://user:pass@host:port
                auth_part, server_part = proxy_url.rsplit("@", 1)
                auth_part = auth_part.replace("http://", "")
                username, password = auth_part.split(":", 1)
                launch_options["proxy"] = {
                    "server": f"http://{server_part}",
                    "username": username,
                    "password": password,
                }
            else:
                launch_options["proxy"] = {"server": proxy_url}
        
        browser = await self._playwright.chromium.launch(**launch_options)
        
        # Context avec fingerprint réaliste
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self._get_random_user_agent(),
            locale="fr-FR",
            timezone_id="Europe/Paris",
            extra_http_headers={
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        
        # Injecter scripts anti-détection
        await context.add_init_script(self._get_stealth_script())
        
        page = await context.new_page()
        
        return BrowserSession(
            browser=browser,
            context=context,
            page=page,
            created_at=time.time(),
        )
    
    async def _close_session(self, session: BrowserSession):
        """Ferme proprement une session."""
        try:
            await session.page.close()
            await session.context.close()
            await session.browser.close()
        except Exception as e:
            logger.warning(f"Error closing browser session: {e}")
    
    async def _cleanup_old_sessions(self):
        """Nettoie les sessions expirées."""
        now = time.time()
        to_remove = []
        
        for session in self._sessions:
            age = now - session.created_at
            if (session.requests_count >= session.max_requests or 
                age >= self.SESSION_MAX_AGE):
                to_remove.append(session)
        
        for session in to_remove:
            await self._close_session(session)
            self._sessions.remove(session)
    
    async def close(self):
        """Ferme tout le pool."""
        async with self._lock:
            for session in self._sessions:
                await self._close_session(session)
            self._sessions.clear()
            
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            
            self._initialized = False
        logger.info("Browser pool closed")
    
    def _get_random_user_agent(self) -> str:
        """Retourne un user-agent aléatoire réaliste."""
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        return random.choice(user_agents)
    
    def _get_stealth_script(self) -> str:
        """Script pour masquer l'automatisation."""
        return """
        // Masquer webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        
        // Masquer automation
        delete navigator.__proto__.webdriver;
        
        // Chrome runtime
        window.chrome = {
            runtime: {},
        };
        
        // Permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        
        // Languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['fr-FR', 'fr', 'en-US', 'en'],
        });
        """


# Singleton global
_browser_pool: Optional[BrowserPool] = None


async def get_browser_pool() -> BrowserPool:
    global _browser_pool
    if _browser_pool is None:
        _browser_pool = BrowserPool()
    return _browser_pool


# =============================================================================
# BROWSER FETCH FUNCTION
# =============================================================================

async def browser_fetch(
    target: str,
    url: str,
    timeout: int = 30,
    wait_for_selector: Optional[str] = None,
    proxy_config: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], ErrorType, Dict]:
    """
    Fetch une URL avec Playwright.
    
    Args:
        target: Nom de la cible
        url: URL à fetcher
        timeout: Timeout en secondes
        wait_for_selector: Sélecteur CSS à attendre (pour SPAs)
    
    Returns:
        (content, error_type, metadata)
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None, ErrorType.BLOCKED, {"error": "Playwright not installed"}
    
    metadata = {
        "method": "browser",
        "proxy_used": False,
    }
    
    start_time = time.time()
    
    try:
        # Obtenir proxy (override ou résidentiel par défaut)
        proxy = proxy_config if proxy_config else get_proxy("residential")
        metadata["proxy_used"] = proxy is not None
        
        pool = await get_browser_pool()
        session = await pool.get_session(proxy)
        
        # Navigation
        response = await session.page.goto(
            url,
            timeout=timeout * 1000,
            wait_until="domcontentloaded",
        )
        
        if response is None:
            return None, ErrorType.NETWORK, metadata
        
        status_code = response.status
        metadata["status_code"] = status_code
        
        # Attendre un sélecteur si spécifié (pour SPAs)
        if wait_for_selector:
            try:
                await session.page.wait_for_selector(
                    wait_for_selector,
                    timeout=10000,
                )
            except Exception:
                logger.debug(f"Selector {wait_for_selector} not found, continuing...")
        
        # Attendre un peu pour le JS
        await asyncio.sleep(1)
        
        # Récupérer le contenu
        content = await session.page.content()
        
        duration_ms = (time.time() - start_time) * 1000
        metadata["duration_ms"] = round(duration_ms, 2)
        metadata["response_size"] = len(content)
        
        if status_code == 200:
            return content, ErrorType.SUCCESS, metadata
        elif status_code == 403:
            return None, ErrorType.HTTP_403, metadata
        elif status_code == 429:
            return None, ErrorType.HTTP_429, metadata
        else:
            return None, ErrorType.BLOCKED, metadata
            
    except asyncio.TimeoutError:
        duration_ms = (time.time() - start_time) * 1000
        metadata["duration_ms"] = round(duration_ms, 2)
        return None, ErrorType.TIMEOUT, metadata
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metadata["duration_ms"] = round(duration_ms, 2)
        metadata["error"] = str(e)
        logger.error(f"Browser fetch error: {e}")
        return None, ErrorType.NETWORK, metadata


# =============================================================================
# SYNCHRONOUS WRAPPER (pour RQ)
# =============================================================================

def browser_fetch_sync(
    target: str,
    url: str,
    timeout: int = 30,
    wait_for_selector: Optional[str] = None,
    proxy_config: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], ErrorType, Dict]:
    """
    Wrapper synchrone pour browser_fetch (utilisable dans les jobs RQ).
    """
    return asyncio.run(browser_fetch(target, url, timeout, wait_for_selector, proxy_config))
