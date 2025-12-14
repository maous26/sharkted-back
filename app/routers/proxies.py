"""
Proxies Router - API pour la gestion des proxies et métriques de scraping.

Endpoints:
- GET /proxies/config - Config actuelle des proxies
- POST /proxies/config - Mettre à jour la config
- GET /proxies/stats - Statistiques d'utilisation
- GET /proxies/orchestrator - Stats de l'orchestrateur
- POST /proxies/test - Tester un proxy
- GET /proxies/templates/{provider} - Template de config pour un provider
"""
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from jose import jwt, JWTError

from app.core.logging import get_logger
from app.core.config import JWT_SECRET, JWT_ALGO
from app.core.proxy_config import (
    load_proxy_config,
    save_proxy_config,
    validate_proxy_config,
    get_proxy_stats,
    get_provider_template,
    generate_example_config,
    KNOWN_PROVIDERS,
)
from app.services.scraping_orchestrator import (
    get_enabled_targets,
    get_disabled_targets,
    is_target_available,
    get_orchestrator,
    configure_proxies,
    get_target_config,
    TARGET_CONFIGS,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/proxies", tags=["proxies"])

# Auth setup
bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Extrait l'utilisateur du token JWT."""
    if not creds:
        raise HTTPException(status_code=401, detail="Missing token")
    
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        return {
            "user_id": payload.get("sub"),
            "email": payload.get("sub"),
            "is_admin": payload.get("is_admin", False),
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Vérifie que l'utilisateur est admin."""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# =============================================================================
# MODELS
# =============================================================================

class ProxyConfigItem(BaseModel):
    provider: str
    type: str = "residential"
    endpoint: str
    username: str
    password: str
    country: str = "FR"
    rotation: str = "rotating"
    session_ttl: int = 300
    enabled: bool = True


class ProxyConfigUpdate(BaseModel):
    datacenter: List[ProxyConfigItem] = []
    residential: List[ProxyConfigItem] = []


class ProxyTestRequest(BaseModel):
    proxy_url: str
    test_url: str = "https://httpbin.org/ip"


# =============================================================================
# ENDPOINTS - PUBLIC (pour monitoring)
# =============================================================================

@router.get("/stats")
def get_stats():
    """
    Statistiques générales sur les proxies configurés.
    
    Ne nécessite pas d'authentification pour le monitoring.
    """
    return get_proxy_stats()


@router.get("/orchestrator")
def get_orchestrator_stats():
    """
    Statistiques de l'orchestrateur de scraping.
    
    Inclut:
    - Méthode actuelle par cible
    - Taux de succès
    - Nombre d'escalades
    - Erreurs récentes
    """
    orchestrator = get_orchestrator()
    return orchestrator.get_stats()


@router.get("/targets")
def get_targets():
    """
    Liste des cibles configurées avec leur niveau de protection.
    """
    targets = []
    for slug, config in TARGET_CONFIGS.items():
        targets.append({
            "slug": slug,
            "name": config.name,
            "protection": config.protection.value,
            "allowed_methods": [m.value for m in config.allowed_methods],
            "requests_per_second": config.requests_per_second,
        })
    return {"targets": targets}


# =============================================================================
# ENDPOINTS - ADMIN ONLY
# =============================================================================

@router.get("/config")
def get_config(current_user: dict = Depends(require_admin)):
    """
    Récupère la configuration actuelle des proxies.
    
    Nécessite des droits admin.
    """
    config = load_proxy_config()
    # Masquer les mots de passe
    for level in ["datacenter", "residential"]:
        for proxy in config.get(level, []):
            if "password" in proxy:
                proxy["password"] = "***"
    return config


@router.post("/config")
def update_config(
    config: ProxyConfigUpdate,
    current_user: dict = Depends(require_admin),
):
    """
    Met à jour la configuration des proxies.
    
    La nouvelle config est validée puis sauvegardée.
    L'orchestrateur est rechargé automatiquement.
    """
    config_dict = {
        "datacenter": [p.model_dump() for p in config.datacenter],
        "residential": [p.model_dump() for p in config.residential],
    }
    
    # Valider
    is_valid, errors = validate_proxy_config(config_dict)
    if not is_valid:
        raise HTTPException(status_code=400, detail={"errors": errors})
    
    # Sauvegarder
    if not save_proxy_config(config_dict):
        raise HTTPException(status_code=500, detail="Failed to save config")
    
    # Recharger l'orchestrateur
    configure_proxies(config_dict)
    
    return {
        "status": "success",
        "message": "Proxy configuration updated",
        "datacenter_count": len(config.datacenter),
        "residential_count": len(config.residential),
    }


@router.post("/test")
async def test_proxy(
    request: ProxyTestRequest,
    current_user: dict = Depends(require_admin),
):
    """
    Teste un proxy en faisant une requête vers une URL de test.
    
    Retourne l'IP visible et le temps de réponse.
    """
    import time
    import httpx
    
    start = time.time()
    try:
        async with httpx.AsyncClient(
            proxies={"http://": request.proxy_url, "https://": request.proxy_url},
            timeout=30,
        ) as client:
            resp = await client.get(request.test_url)
            duration_ms = (time.time() - start) * 1000
            
            return {
                "status": "success",
                "status_code": resp.status_code,
                "duration_ms": round(duration_ms, 2),
                "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:500],
            }
    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        return {
            "status": "error",
            "error": str(e),
            "duration_ms": round(duration_ms, 2),
        }


@router.get("/templates")
def get_templates():
    """
    Retourne les templates de configuration pour tous les providers connus.
    """
    templates = {}
    for provider in KNOWN_PROVIDERS:
        templates[provider] = {
            "residential": get_provider_template(provider, "residential"),
            "datacenter": get_provider_template(provider, "datacenter"),
        }
    return templates


@router.get("/templates/{provider}")
def get_provider_config_template(provider: str, proxy_type: str = "residential"):
    """
    Retourne un template de configuration pour un provider spécifique.
    """
    template = get_provider_template(provider, proxy_type)
    return template


@router.get("/example-config")
def get_example_config():
    """
    Génère une configuration d'exemple complète avec tous les providers.
    """
    return generate_example_config()


@router.post("/reset/{target}")
def reset_target_stats(
    target: str,
    current_user: dict = Depends(require_admin),
):
    """
    Reset les statistiques de l'orchestrateur pour une cible.
    
    Utile après avoir corrigé un problème ou changé la config.
    """
    orchestrator = get_orchestrator()
    orchestrator.engine.reset_target(target)
    
    return {
        "status": "success",
        "message": f"Stats reset for target: {target}",
    }
