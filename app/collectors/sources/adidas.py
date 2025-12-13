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
    Récupère et parse un produit Adidas.

    Raises:
        BlockedError: Si bloqué par Akamai (403)
        TimeoutError: Si timeout réseau
        NetworkError: Si erreur réseau
        HTTPError: Si erreur HTTP autre
        DataExtractionError: Si pas de JSON-LD trouvé
        ValidationError: Si données invalides
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    try:
        resp = scraper.get(url, timeout=30)
    except requests.exceptions.Timeout as e:
        raise TimeoutError(
            f"Timeout après 30s",
            source=SOURCE,
            url=url,
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise NetworkError(
            f"Erreur de connexion: {e}",
            source=SOURCE,
            url=url,
        ) from e
    except requests.exceptions.RequestException as e:
        raise NetworkError(
            f"Erreur réseau: {e}",
            source=SOURCE,
            url=url,
        ) from e

    # Vérifier le blocage
    if _is_blocked(resp.text, resp.status_code):
        raise BlockedError(
            f"Bloqué par protection Akamai",
            source=SOURCE,
            url=url,
            status_code=resp.status_code,
        )

    # Vérifier le status HTTP
    if resp.status_code >= 400:
        raise HTTPError(
            f"Erreur HTTP",
            status_code=resp.status_code,
            source=SOURCE,
            url=url,
        )

    # Extraire le JSON-LD
    prod = _extract_product_from_jsonld(resp.text)
    if not prod:
        raise DataExtractionError(
            "Aucun Product JSON-LD trouvé dans la page",
            source=SOURCE,
            url=url,
        )

    # Parser les données
    title = prod.get("name")
    if not title:
        raise ValidationError(
            "Titre du produit manquant",
            field="name",
            source=SOURCE,
            url=url,
        )

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

    if price <= 0:
        raise ValidationError(
            f"Prix invalide: {offers.get('price')}",
            field="price",
            source=SOURCE,
            url=url,
        )

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
