"""
Collector Adidas - Extraction de produits via JSON-LD.

Note: Adidas utilise Akamai comme protection anti-bot.
Ce collector est actuellement bloqué (403) et mis en pause.
Il sera réactivé quand l'infrastructure proxy sera prête.
"""
import json
import re
from typing import Optional

import cloudscraper
import requests.exceptions

from app.normalizers.item import DealItem
from app.core.exceptions import (
    BlockedError,
    HTTPError,
    NetworkError,
    TimeoutError,
    DataExtractionError,
    ValidationError,
)

SOURCE = "adidas"

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Indicateurs de blocage Akamai
_BLOCK_INDICATORS = [
    "access denied",
    "reference #",
    "akamai",
    "blocked",
    "please enable javascript",
]


def _is_blocked(html: str, status_code: int) -> bool:
    """Détecte si la réponse indique un blocage anti-bot."""
    if status_code in (403, 503):
        return True
    html_lower = html.lower()
    return any(indicator in html_lower for indicator in _BLOCK_INDICATORS)


def _extract_product_from_jsonld(html: str) -> Optional[dict]:
    """Extrait le JSON-LD Product du HTML."""
    for match in _JSONLD_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidates = []
        if isinstance(data, dict):
            if "@graph" in data:
                candidates = data["@graph"]
            else:
                candidates = [data]
        elif isinstance(data, list):
            candidates = data

        for obj in candidates:
            if isinstance(obj, dict):
                t = obj.get("@type")
                if t == "Product" or (isinstance(t, list) and "Product" in t):
                    return obj
    return None


def fetch_adidas_product(url: str) -> DealItem:
    """
    Récupère et parse un produit Adidas via Playwright + Proxy.
    Contourne Akamai.
    """
    from app.services.browser_worker import browser_fetch_sync
    from app.services.proxy_service import get_web_unlocker_proxy
    
    proxy = get_web_unlocker_proxy()
    
    # 1. Fetch via Browser Worker
    content, error, meta = browser_fetch_sync(
        target="adidas",
        url=url,
        timeout=60, # Akamai peut être lent
        wait_for_selector='script[type="application/ld+json"]', # Attendre le JSON-LD
        proxy_config=proxy
    )
    
    if error.value != "success" or not content:
        # Mapper les erreurs
        if error.value == "blocked":
            raise BlockedError("Bloqué par Akamai", source=SOURCE, url=url)
        elif error.value == "timeout":
            raise TimeoutError("Timeout browser", source=SOURCE, url=url)
        else:
            raise NetworkError(f"Erreur fetch: {error.value}", source=SOURCE, url=url)

    # 2. Parsing JSON-LD (réutilisation de la logique existante)
    prod = _extract_product_from_jsonld(content)
    if not prod:
        raise DataExtractionError(
            "Aucun Product JSON-LD trouvé (après JS)",
            source=SOURCE,
            url=url,
        )

    # 3. Extraction standard
    title = prod.get("name")
    if not title:
        raise ValidationError("Titre manquant", field="name", source=SOURCE, url=url)

    image = None
    if isinstance(prod.get("image"), list) and prod["image"]:
        image = prod["image"][0]
    elif isinstance(prod.get("image"), str):
        image = prod["image"]

    offers = prod.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]

    try:
        price = float(offers.get("price", 0))
    except (ValueError, TypeError):
        price = 0.0
        
    # Adidas affiche parfois le prix plein sans discount dans le JSON-LD principal
    # On pourrait parser le HTML pour trouver le prix remisé si besoin
    
    return DealItem(
        source=SOURCE,
        external_id=str(prod.get("sku") or url),
        title=title,
        price=price,
        currency=offers.get("priceCurrency", "EUR"),
        url=url,
        image_url=image,
        raw=prod,
    )
