"""
Proxy Configuration - Chargement et gestion de la config des proxies.

La config peut venir de:
1. Variables d'environnement (PROXY_CONFIG_JSON)
2. Fichier config (config/proxies.json)
3. Base de données (pour admin UI)

Format JSON attendu:
{
    "datacenter": [
        {
            "provider": "example",
            "type": "datacenter",
            "endpoint": "proxy.example.com:8080",
            "username": "user",
            "password": "pass",
            "country": "FR",
            "rotation": "rotating",
            "enabled": true
        }
    ],
    "residential": [
        {
            "provider": "smartproxy",
            "type": "residential",
            "endpoint": "gate.smartproxy.com:7000",
            "username": "user",
            "password": "pass",
            "country": "FR",
            "rotation": "rotating",
            "enabled": true
        }
    ]
}

Providers supportés:
- Smartproxy (residential, datacenter)
- Bright Data (residential, datacenter, mobile)
- Oxylabs (residential, datacenter)
- IPRoyal (residential)
- Custom (any HTTP proxy)
"""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


# Chemins de config
CONFIG_DIR = Path("/opt/sharkted-api/config")
PROXY_CONFIG_FILE = CONFIG_DIR / "proxies.json"


@dataclass
class ProxyProviderInfo:
    """Info sur un provider de proxy."""
    name: str
    endpoint_format: str
    supports_sticky: bool
    supports_country: bool
    auth_format: str  # "basic" ou "url"
    
    
# Providers connus avec leur format
KNOWN_PROVIDERS = {
    "smartproxy": ProxyProviderInfo(
        name="Smartproxy",
        endpoint_format="gate.smartproxy.com:7000",
        supports_sticky=True,
        supports_country=True,
        auth_format="basic",
    ),
    "brightdata": ProxyProviderInfo(
        name="Bright Data",
        endpoint_format="brd.superproxy.io:22225",
        supports_sticky=True,
        supports_country=True,
        auth_format="basic",
    ),
    "oxylabs": ProxyProviderInfo(
        name="Oxylabs",
        endpoint_format="pr.oxylabs.io:7777",
        supports_sticky=True,
        supports_country=True,
        auth_format="basic",
    ),
    "iproyal": ProxyProviderInfo(
        name="IPRoyal",
        endpoint_format="geo.iproyal.com:12321",
        supports_sticky=True,
        supports_country=True,
        auth_format="basic",
    ),
}


def load_proxy_config() -> Dict[str, Any]:
    """
    Charge la configuration des proxies.
    
    Ordre de priorité:
    1. Variable d'environnement PROXY_CONFIG_JSON
    2. Fichier config/proxies.json
    3. Config vide par défaut
    """
    config = {"datacenter": [], "residential": []}
    
    # 1. Essayer variable d'environnement
    env_config = os.getenv("PROXY_CONFIG_JSON")
    if env_config:
        try:
            config = json.loads(env_config)
            logger.info("Proxy config loaded from environment variable")
            return config
        except json.JSONDecodeError as e:
            logger.error(f"Invalid PROXY_CONFIG_JSON: {e}")
    
    # 2. Essayer fichier config
    if PROXY_CONFIG_FILE.exists():
        try:
            with open(PROXY_CONFIG_FILE) as f:
                config = json.load(f)
            logger.info(f"Proxy config loaded from {PROXY_CONFIG_FILE}")
            return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load {PROXY_CONFIG_FILE}: {e}")
    
    # 3. Config vide
    logger.info("No proxy config found, using empty config")
    return config


def save_proxy_config(config: Dict[str, Any]) -> bool:
    """Sauvegarde la config dans le fichier."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(PROXY_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.info(f"Proxy config saved to {PROXY_CONFIG_FILE}")
        return True
    except IOError as e:
        logger.error(f"Failed to save proxy config: {e}")
        return False


def validate_proxy_config(config: Dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Valide une configuration de proxy.
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    
    for level in ["datacenter", "residential"]:
        proxies = config.get(level, [])
        if not isinstance(proxies, list):
            errors.append(f"{level} must be a list")
            continue
            
        for i, proxy in enumerate(proxies):
            prefix = f"{level}[{i}]"
            
            if not isinstance(proxy, dict):
                errors.append(f"{prefix} must be an object")
                continue
            
            # Champs requis
            required = ["provider", "endpoint", "username", "password"]
            for field in required:
                if not proxy.get(field):
                    errors.append(f"{prefix}.{field} is required")
            
            # Type valide
            proxy_type = proxy.get("type", level)
            if proxy_type not in ["datacenter", "residential", "mobile"]:
                errors.append(f"{prefix}.type must be datacenter, residential, or mobile")
            
            # Rotation valide
            rotation = proxy.get("rotation", "rotating")
            if rotation not in ["rotating", "sticky"]:
                errors.append(f"{prefix}.rotation must be rotating or sticky")
    
    return len(errors) == 0, errors


def get_proxy_stats() -> Dict[str, Any]:
    """Retourne des statistiques sur la config des proxies."""
    config = load_proxy_config()
    
    return {
        "datacenter": {
            "total": len(config.get("datacenter", [])),
            "enabled": len([p for p in config.get("datacenter", []) if p.get("enabled", True)]),
            "providers": list(set(p.get("provider") for p in config.get("datacenter", []))),
        },
        "residential": {
            "total": len(config.get("residential", [])),
            "enabled": len([p for p in config.get("residential", []) if p.get("enabled", True)]),
            "providers": list(set(p.get("provider") for p in config.get("residential", []))),
        },
        "config_file": str(PROXY_CONFIG_FILE),
        "config_exists": PROXY_CONFIG_FILE.exists(),
    }


# =============================================================================
# TEMPLATES DE CONFIGURATION PAR PROVIDER
# =============================================================================

def get_provider_template(provider: str, proxy_type: str = "residential") -> Dict[str, Any]:
    """Retourne un template de configuration pour un provider."""
    templates = {
        "smartproxy": {
            "residential": {
                "provider": "smartproxy",
                "type": "residential",
                "endpoint": "gate.smartproxy.com:7000",
                "username": "YOUR_USERNAME",
                "password": "YOUR_PASSWORD",
                "country": "FR",
                "rotation": "rotating",
                "enabled": True,
            },
            "datacenter": {
                "provider": "smartproxy",
                "type": "datacenter",
                "endpoint": "gate.smartproxy.com:7000",
                "username": "YOUR_USERNAME",
                "password": "YOUR_PASSWORD",
                "country": "FR",
                "rotation": "rotating",
                "enabled": True,
            },
        },
        "brightdata": {
            "residential": {
                "provider": "brightdata",
                "type": "residential",
                "endpoint": "brd.superproxy.io:22225",
                "username": "YOUR_CUSTOMER_ID-zone-YOUR_ZONE",
                "password": "YOUR_PASSWORD",
                "country": "FR",
                "rotation": "rotating",
                "enabled": True,
            },
        },
        "oxylabs": {
            "residential": {
                "provider": "oxylabs",
                "type": "residential",
                "endpoint": "pr.oxylabs.io:7777",
                "username": "customer-YOUR_USERNAME-cc-FR",
                "password": "YOUR_PASSWORD",
                "country": "FR",
                "rotation": "rotating",
                "enabled": True,
            },
        },
        "iproyal": {
            "residential": {
                "provider": "iproyal",
                "type": "residential",
                "endpoint": "geo.iproyal.com:12321",
                "username": "YOUR_USERNAME",
                "password": "YOUR_PASSWORD_country-fr",
                "country": "FR",
                "rotation": "rotating",
                "enabled": True,
            },
        },
    }
    
    return templates.get(provider, {}).get(proxy_type, {
        "provider": provider,
        "type": proxy_type,
        "endpoint": "proxy.example.com:8080",
        "username": "YOUR_USERNAME",
        "password": "YOUR_PASSWORD",
        "country": "FR",
        "rotation": "rotating",
        "enabled": True,
    })


def generate_example_config() -> Dict[str, Any]:
    """Génère une config d'exemple avec tous les providers."""
    return {
        "datacenter": [
            get_provider_template("smartproxy", "datacenter"),
        ],
        "residential": [
            get_provider_template("smartproxy", "residential"),
            get_provider_template("brightdata", "residential"),
            get_provider_template("oxylabs", "residential"),
            get_provider_template("iproyal", "residential"),
        ],
    }
