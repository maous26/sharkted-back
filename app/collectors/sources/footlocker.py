"""
Collector Footlocker FR - Extraction de produits via JSON-LD.

Footlocker.fr est accessible via cloudscraper et fournit un JSON-LD Product complet.
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
from app.utils.retry import retry_on_network_errors

SOURCE = "footlocker"

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL (ex: .../314217910604.html -> 314217910604)."""
    match = re.search(r'/(\d{10,})\.html', url)
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """
    Extrait les données produit depuis le JSON-LD.
    Footlocker fournit un JSON-LD Product complet.
    """
    data = {
        "name": None,
        "price": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
    }

    # Chercher le JSON-LD Product
    for match in _JSONLD_RE.finditer(html):
        try:
            jsonld = json.loads(match.group(1).strip())
            if isinstance(jsonld, dict) and jsonld.get("@type") == "Product":
                data["name"] = jsonld.get("name")
                data["brand"] = jsonld.get("brand")
                data["sku"] = jsonld.get("sku") or data["sku"]

                # Image peut être string ou array
                image = jsonld.get("image")
                if isinstance(image, list):
                    data["image"] = image[0] if image else None
                else:
                    data["image"] = image

                # Prix dans offers
                offers = jsonld.get("offers", {})
                if isinstance(offers, dict):
                    data["price"] = offers.get("price")
                    data["currency"] = offers.get("priceCurrency", "EUR")
                elif isinstance(offers, list) and offers:
                    data["price"] = offers[0].get("price")
                    data["currency"] = offers[0].get("priceCurrency", "EUR")

                break
        except (json.JSONDecodeError, KeyError):
            continue

    # Fallback: meta tags si JSON-LD incomplet
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).strip()

    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_footlocker_product(url: str) -> DealItem:
    """
    Récupère et parse un produit Footlocker FR.

    Raises:
        BlockedError: Si bloqué
        TimeoutError: Si timeout réseau
        NetworkError: Si erreur réseau
        HTTPError: Si erreur HTTP autre
        DataExtractionError: Si données non trouvées
        ValidationError: Si données invalides
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    try:
        resp = scraper.get(url, timeout=30)
    except requests.exceptions.Timeout as e:
        raise TimeoutError(
            "Timeout après 30s",
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

    # Vérifier le status HTTP
    if resp.status_code == 403:
        raise BlockedError(
            "Bloqué par protection anti-bot",
            source=SOURCE,
            url=url,
            status_code=403,
        )

    if resp.status_code == 404:
        raise DataExtractionError(
            "Produit non trouvé (404)",
            source=SOURCE,
            url=url,
        )

    if resp.status_code >= 400:
        raise HTTPError(
            "Erreur HTTP",
            status_code=resp.status_code,
            source=SOURCE,
            url=url,
        )

    # Extraire les données
    data = _extract_product_data(resp.text, url)

    # Validation
    if not data["name"]:
        raise DataExtractionError(
            "Nom du produit non trouvé",
            source=SOURCE,
            url=url,
        )

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(
            f"Prix invalide: {data['price']}",
            field="price",
            source=SOURCE,
            url=url,
        )

    # Construire l'external_id
    external_id = data["sku"] or url.split("/")[-1].replace(".html", "")

    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data["name"],
        price=data["price"],
        currency=data["currency"],
        url=url,
        image_url=data["image"],
        seller_name=data["brand"],
        raw=data,
    )
