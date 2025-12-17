"""
Browser Worker - Playwright pour les sites SPA/JS.
Version simplifiée sans pool (plus stable).
"""
import asyncio
import random
import time
from typing import Optional, Dict, Tuple, Any

from app.core.logging import get_logger
from app.services.scraping_orchestrator import ErrorType

logger = get_logger(__name__)

# Flag pour vérifier si Playwright est disponible
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    logger.warning("Playwright not installed - browser scraping disabled")


def _get_random_user_agent() -> str:
    """Retourne un user-agent aléatoire réaliste."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]
    return random.choice(user_agents)


def _get_stealth_script() -> str:
    """Script pour masquer l'automatisation."""
    return """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'] });
    """


async def browser_fetch(
    target: str,
    url: str,
    timeout: int = 30,
    wait_for_selector: Optional[str] = None,
    proxy_config: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], ErrorType, Dict]:
    """
    Fetch une URL avec Playwright (nouvelle instance à chaque fois).
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None, ErrorType.BLOCKED, {"error": "Playwright not installed"}
    
    metadata = {
        "method": "browser",
        "proxy_used": False,
    }
    
    start_time = time.time()
    playwright = None
    browser = None
    
    try:
        playwright = await async_playwright().start()
        
        launch_options = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }
        
        # Configurer proxy si fourni
        if proxy_config:
            proxy_url = proxy_config.get("http", "")
            if "@" in proxy_url:
                auth_part, server_part = proxy_url.rsplit("@", 1)
                auth_part = auth_part.replace("http://", "")
                username, password = auth_part.split(":", 1)
                launch_options["proxy"] = {
                    "server": f"http://{server_part}",
                    "username": username,
                    "password": password,
                }
                metadata["proxy_used"] = True
            else:
                launch_options["proxy"] = {"server": proxy_url}
                metadata["proxy_used"] = True
        
        browser = await playwright.chromium.launch(**launch_options)
        
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=_get_random_user_agent(),
            locale="fr-FR",
            timezone_id="Europe/Paris",
            extra_http_headers={
                "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            ignore_https_errors=True,
        )
        
        await context.add_init_script(_get_stealth_script())
        page = await context.new_page()
        
        # Navigation
        response = await page.goto(
            url,
            timeout=timeout * 1000,
            wait_until="domcontentloaded",
        )
        
        if response is None:
            return None, ErrorType.NETWORK, metadata
        
        status_code = response.status
        metadata["status_code"] = status_code
        
        # Attendre un sélecteur si spécifié
        if wait_for_selector:
            try:
                await page.wait_for_selector(wait_for_selector, timeout=10000)
            except Exception:
                logger.debug(f"Selector {wait_for_selector} not found, continuing...")
        
        # Attendre que le JS charge le contenu
        await asyncio.sleep(3)
        
        # Récupérer le contenu avec retry
        content = None
        for attempt in range(3):
            try:
                content = await page.content()
                break
            except Exception as nav_err:
                if "navigating" in str(nav_err).lower() and attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise
        
        duration_ms = (time.time() - start_time) * 1000
        metadata["duration_ms"] = round(duration_ms, 2)
        metadata["response_size"] = len(content) if content else 0
        
        # Fermer proprement
        await page.close()
        await context.close()
        await browser.close()
        await playwright.stop()
        
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
    finally:
        # Nettoyage en cas d'erreur
        try:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
        except:
            pass


def browser_fetch_sync(
    target: str,
    url: str,
    timeout: int = 30,
    wait_for_selector: Optional[str] = None,
    proxy_config: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], ErrorType, Dict]:
    """Wrapper synchrone pour browser_fetch - gère les nested event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    
    if loop and loop.is_running():
        # Déjà dans un event loop - utiliser un thread séparé
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, browser_fetch(target, url, timeout, wait_for_selector, proxy_config))
            return future.result()
    else:
        # Pas d'event loop actif - utiliser asyncio.run directement
        return asyncio.run(browser_fetch(target, url, timeout, wait_for_selector, proxy_config))
