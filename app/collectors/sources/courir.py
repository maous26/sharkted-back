"""
Collector Courir - Extraction de produits via parsing HTML + JSON-LD.

Courir.com est accessible via cloudscraper mais le JSON-LD Product est incomplet.
On complète avec le parsing HTML.
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

SOURCE = "courir"

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL (ex: vans-era-floral-1489580.html -> 1489580)."""
    match = re.search(r'-(\d{6,})\.html', url)
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """
    Extrait les données produit depuis le HTML.
    Combine JSON-LD + parsing HTML pour données complètes.
    """
    data = {
        "name": None,
        "price": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
    }

    # 1. Essayer JSON-LD
    for match in _JSONLD_RE.finditer(html):
        try:
            jsonld = json.loads(match.group(1).strip())
            if isinstance(jsonld, dict) and jsonld.get("@type") == "Product":
                data["brand"] = jsonld.get("name")  # C'est la marque, pas le nom complet
                offers = jsonld.get("offers", {})
                if isinstance(offers, dict):
                    # lowPrice est le prix de vente
                    data["price"] = offers.get("lowPrice") or offers.get("price")
                    data["currency"] = offers.get("priceCurrency", "EUR")
        except (json.JSONDecodeError, KeyError):
            continue

    # 2. Compléter avec meta tags
    og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
    og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)

    if og_title:
        data["name"] = og_title.group(1).strip()
    if og_image:
        data["image"] = og_image.group(1)

    # 3. Fallback: titre de la page
    if not data["name"]:
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            # Format: "Nom Produit | Courir"
            title = title_match.group(1).split("|")[0].strip()
            data["name"] = title

    # 4. Fallback prix: chercher dans le HTML
    if not data["price"]:
        # Pattern prix Courir
        price_match = re.search(r'class="[^"]*current-price[^"]*"[^>]*>([0-9,.]+)\s*€', html)
        if not price_match:
            price_match = re.search(r'data-price="([0-9,.]+)"', html)
        if not price_match:
            price_match = re.search(r'"price":\s*"?([0-9]+(?:[.,][0-9]+)?)"?', html)
        if price_match:
            price_str = price_match.group(1).replace(",", ".")
            try:
                data["price"] = float(price_str)
            except ValueError:
                pass

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_courir_product(url: str) -> DealItem:
    """
    Récupère et parse un produit Courir.

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
