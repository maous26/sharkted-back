"""
HTTP Stealth - Utilitaires anti-détection pour le scraping.

Fournit:
- Rotation User-Agent réaliste
- Headers complets simulant un vrai navigateur  
- Délais aléatoires entre requêtes
- Session cloudscraper préconfigurée
"""
import random
import time
from typing import Dict, Optional, Tuple
import cloudscraper

# Pool de User-Agents réalistes (Chrome/Firefox récents)
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

# Headers de base pour simuler un vrai navigateur
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Configurations de délais par source
DELAY_CONFIGS = {
    "courir": {"min": 2.0, "max": 4.0},
    "footlocker": {"min": 2.5, "max": 5.0},
    "size": {"min": 2.0, "max": 4.5},
    "jdsports": {"min": 1.5, "max": 3.5},
    "default": {"min": 2.0, "max": 4.0},
}


def get_random_user_agent() -> str:
    """Retourne un User-Agent aléatoire."""
    return random.choice(USER_AGENTS)


def get_stealth_headers(referer: Optional[str] = None) -> Dict[str, str]:
    """
    Retourne des headers complets simulant un vrai navigateur.
    
    Args:
        referer: URL de référence optionnelle
    
    Returns:
        Dict de headers
    """
    headers = BASE_HEADERS.copy()
    headers["User-Agent"] = get_random_user_agent()
    
    if referer:
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    
    # Varier légèrement Sec-Ch-Ua selon le User-Agent
    ua = headers["User-Agent"]
    if "Firefox" in ua:
        del headers["Sec-Ch-Ua"]
        del headers["Sec-Ch-Ua-Mobile"]
        del headers["Sec-Ch-Ua-Platform"]
    elif "Safari" in ua and "Chrome" not in ua:
        del headers["Sec-Ch-Ua"]
        del headers["Sec-Ch-Ua-Mobile"]
        del headers["Sec-Ch-Ua-Platform"]
    elif "Macintosh" in ua:
        headers["Sec-Ch-Ua-Platform"] = '"macOS"'
    
    return headers


def random_delay(source: str = "default", multiplier: float = 1.0) -> float:
    """
    Applique un délai aléatoire et retourne la durée.
    
    Args:
        source: Nom de la source pour config spécifique
        multiplier: Multiplicateur de délai (ex: 1.5 pour +50%)
    
    Returns:
        Durée du délai en secondes
    """
    config = DELAY_CONFIGS.get(source, DELAY_CONFIGS["default"])
    delay = random.uniform(config["min"], config["max"]) * multiplier
    
    # Ajouter un jitter de ±20%
    jitter = delay * random.uniform(-0.2, 0.2)
    delay = max(0.5, delay + jitter)
    
    time.sleep(delay)
    return delay


def create_stealth_scraper(source: str = "default") -> Tuple[cloudscraper.CloudScraper, Dict[str, str]]:
    """
    Crée un scraper cloudscraper avec headers stealth.
    
    Args:
        source: Nom de la source
    
    Returns:
        Tuple (scraper, headers)
    """
    # Choisir un browser cohérent avec le User-Agent
    ua = get_random_user_agent()
    
    if "Firefox" in ua:
        browser = {"browser": "firefox", "platform": "windows", "mobile": False}
    elif "Safari" in ua and "Chrome" not in ua:
        browser = {"browser": "chrome", "platform": "darwin", "mobile": False}
    else:
        platform = "darwin" if "Macintosh" in ua else "windows"
        browser = {"browser": "chrome", "platform": platform, "mobile": False}
    
    scraper = cloudscraper.create_scraper(browser=browser)
    headers = get_stealth_headers()
    
    return scraper, headers


def get_source_delay_config(source: str) -> Dict[str, float]:
    """Retourne la config de délai pour une source."""
    return DELAY_CONFIGS.get(source, DELAY_CONFIGS["default"])


# =============================================================================
# PROXY SUPPORT
# =============================================================================

import json
import os
from pathlib import Path

_proxy_config_cache = None
_proxy_config_mtime = 0


def _load_proxy_config() -> dict:
    """Charge la config proxy depuis le fichier JSON."""
    global _proxy_config_cache, _proxy_config_mtime
    
    config_path = Path("/opt/sharkted-api/config/proxies.json")
    
    if not config_path.exists():
        return {"datacenter": [], "residential": []}
    
    # Recharger si le fichier a été modifié
    mtime = config_path.stat().st_mtime
    if _proxy_config_cache is None or mtime > _proxy_config_mtime:
        with open(config_path, 'r') as f:
            _proxy_config_cache = json.load(f)
        _proxy_config_mtime = mtime
    
    return _proxy_config_cache


def get_proxy() -> Optional[Dict[str, str]]:
    """
    Retourne un proxy aléatoire parmi les datacenter proxies actifs.
    
    Returns:
        Dict au format requests {"http": ..., "https": ...} ou None
    """
    config = _load_proxy_config()
    
    # Filtrer les proxies actifs
    active_proxies = [p for p in config.get("datacenter", []) if p.get("enabled", False)]
    
    if not active_proxies:
        return None
    
    # Sélection pondérée
    weights = [p.get("weight", 1) for p in active_proxies]
    total_weight = sum(weights)
    r = random.uniform(0, total_weight)
    
    cumulative = 0
    selected = active_proxies[0]
    for proxy, weight in zip(active_proxies, weights):
        cumulative += weight
        if r <= cumulative:
            selected = proxy
            break
    
    # Construire l'URL du proxy
    protocol = selected.get("protocol", "http")
    endpoint = selected.get("endpoint")
    
    if not endpoint:
        return None
    
    # Auth si présente
    username = selected.get("username")
    password = selected.get("password")
    
    if username and password:
        proxy_url = f"{protocol}://{username}:{password}@{endpoint}"
    else:
        proxy_url = f"{protocol}://{endpoint}"
    
    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def create_stealth_scraper_with_proxy(source: str = "default") -> Tuple[cloudscraper.CloudScraper, Dict[str, str], Optional[Dict[str, str]]]:
    """
    Crée un scraper avec headers stealth ET proxy si disponible.
    
    Returns:
        Tuple (scraper, headers, proxies)
    """
    scraper, headers = create_stealth_scraper(source)
    proxies = get_proxy()
    
    return scraper, headers, proxies


def should_use_proxy(source: str) -> bool:
    """
    Détermine si on doit utiliser un proxy pour cette source.
    
    Logique:
    - 50% des requêtes passent par le proxy (rotation)
    - Certaines sources peuvent forcer le proxy
    """
    # Sources qui nécessitent toujours un proxy
    force_proxy_sources = {"footlocker", "adidas"}
    
    if source in force_proxy_sources:
        return True
    
    # Sinon 50/50
    return random.random() < 0.5
