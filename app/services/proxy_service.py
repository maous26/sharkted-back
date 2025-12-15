"""
Proxy Service - Charge et gère les proxies depuis la DB.

Ce service fait le pont entre:
- La configuration admin (ProxySettings en DB)
- Le scraping_orchestrator (qui a besoin de proxies)
- Le premium_gate (qui trace les coûts)
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from loguru import logger

from app.db.session import SessionLocal
from app.models.proxy_settings import ProxySettings


# Cache pour éviter les queries DB à chaque requête
_proxy_cache: Dict[str, Any] = {}
_cache_expiry: Optional[datetime] = None
CACHE_TTL = timedelta(minutes=2)


def _refresh_cache() -> None:
    """Rafraîchit le cache des proxies depuis la DB."""
    global _proxy_cache, _cache_expiry
    
    try:
        db = SessionLocal()
        try:
            proxies = db.query(ProxySettings).filter(
                ProxySettings.enabled == True
            ).all()
            
            _proxy_cache = {
                "datacenter": [],
                "residential": [],
                "web_unlocker": [],
            }
            
            for p in proxies:
                proxy_type = p.proxy_type.lower()
                if proxy_type in _proxy_cache:
                    _proxy_cache[proxy_type].append({
                        "id": p.id,
                        "name": p.name,
                        "provider": p.provider,
                        "url": p.get_proxy_url(),
                        "host": p.host,
                        "port": p.port,
                        "username": p.username,
                        "password": p.password,
                        "is_default": p.is_default,
                    })
            
            _cache_expiry = datetime.utcnow() + CACHE_TTL
            logger.debug(f"Proxy cache refreshed: {len(proxies)} proxies loaded")
            
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to refresh proxy cache: {e}")


def _ensure_cache() -> None:
    """S'assure que le cache est valide."""
    global _cache_expiry
    if _cache_expiry is None or datetime.utcnow() > _cache_expiry:
        _refresh_cache()


def get_proxy_for_scraping(proxy_type: str = "web_unlocker") -> Optional[Dict[str, str]]:
    """
    Retourne un proxy pour le scraping.
    
    Args:
        proxy_type: "datacenter", "residential", ou "web_unlocker"
    
    Returns:
        Dict avec "http" et "https" pour requests/cloudscraper
    """
    _ensure_cache()
    
    proxies = _proxy_cache.get(proxy_type, [])
    if not proxies:
        logger.warning(f"No {proxy_type} proxy configured")
        return None
    
    # Prendre le default ou le premier
    proxy = next((p for p in proxies if p["is_default"]), proxies[0])
    
    url = proxy["url"]
    return {"http": url, "https": url}


def get_web_unlocker_proxy() -> Optional[Dict[str, str]]:
    """Raccourci pour obtenir le proxy Web Unlocker."""
    return get_proxy_for_scraping("web_unlocker")


def has_web_unlocker_configured() -> bool:
    """Vérifie si un Web Unlocker est configuré."""
    _ensure_cache()
    return len(_proxy_cache.get("web_unlocker", [])) > 0


def has_residential_configured() -> bool:
    """Vérifie si des proxies résidentiels sont configurés."""
    _ensure_cache()
    return len(_proxy_cache.get("residential", [])) > 0


def get_proxy_stats() -> Dict[str, Any]:
    """Retourne les stats des proxies configurés."""
    _ensure_cache()
    return {
        "datacenter_count": len(_proxy_cache.get("datacenter", [])),
        "residential_count": len(_proxy_cache.get("residential", [])),
        "web_unlocker_count": len(_proxy_cache.get("web_unlocker", [])),
        "has_web_unlocker": has_web_unlocker_configured(),
        "cache_valid_until": _cache_expiry.isoformat() if _cache_expiry else None,
    }


def record_proxy_usage(proxy_id: int, success: bool) -> None:
    """Enregistre l'utilisation d'un proxy (succès/échec)."""
    try:
        db = SessionLocal()
        try:
            proxy = db.query(ProxySettings).filter(ProxySettings.id == proxy_id).first()
            if proxy:
                proxy.last_used_at = datetime.utcnow()
                if success:
                    proxy.success_count += 1
                else:
                    proxy.error_count += 1
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to record proxy usage: {e}")


def invalidate_cache() -> None:
    """Force le rechargement du cache."""
    global _cache_expiry
    _cache_expiry = None
    logger.info("Proxy cache invalidated")


def get_proxy_pool() -> Dict[str, Any]:
    """
    Retourne le pool de proxies pour compatibilité avec sources.py.
    
    Returns:
        Dict avec datacenter, residential, web_unlocker lists
    """
    _ensure_cache()
    return _proxy_cache.copy()
