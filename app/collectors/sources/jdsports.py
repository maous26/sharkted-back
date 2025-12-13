"""
Collector JD Sports FR - Extraction de produits via meta tags et JavaScript.

JD Sports FR (jdsports.fr) est accessible via cloudscraper.
Les données produit sont dans:
- Meta tags Twitter (twitter:data1 = price, twitter:image:src = image)
- Meta tags standards (name="title" = nom)
- Variables JavaScript (brand, plu, unitPrice)
"""
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

SOURCE = "jdsports"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """
    Extrait le SKU de l'URL.
    Format: /product/xxx-product-name/19727805_jdsportsfr/
    """
    match = re.search(r'/(\d+_jdsportsfr)/?', url)
    if match:
        return match.group(1)
    # Fallback: dernier segment numérique
    match = re.search(r'/(\d+)/?$', url.rstrip('/'))
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML."""
    data = {
        "name": None,
        "price": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
    }

    # 1. Meta name="title" pour le nom complet
    title_meta = re.search(r'<meta name="title"\s+content="([^"]+)"', html, re.IGNORECASE)
    if title_meta:
        # Format: "New Balance ABZORB 2000 Noir- JD Sports France"
        title = title_meta.group(1).strip()
        # Supprimer le suffixe JD Sports
        title = re.sub(r'\s*-?\s*JD Sports.*$', '', title, flags=re.IGNORECASE)
        data["name"] = title.strip()

    # 2. Twitter meta pour le prix
    price_meta = re.search(r'<meta name="twitter:data1"\s+content="([^"]+)"', html, re.IGNORECASE)
    if price_meta:
        try:
            price_str = price_meta.group(1).replace(",", ".")
            data["price"] = float(price_str)
        except ValueError:
            pass

    # 3. Twitter meta pour l'image
    image_meta = re.search(r'<meta name="twitter:image:src"\s+content="([^"]+)"', html, re.IGNORECASE)
    if image_meta:
        data["image"] = image_meta.group(1)

    # 4. JavaScript pour la marque
    brand_js = re.search(r'brand:\s*"([^"]+)"', html)
    if brand_js:
        data["brand"] = brand_js.group(1)

    # 5. JavaScript pour le PLU (SKU)
    plu_js = re.search(r'plu:\s*"([^"]+)"', html)
    if plu_js:
        data["sku"] = plu_js.group(1)

    # 6. Fallback prix depuis JavaScript
    if not data["price"]:
        price_js = re.search(r'unitPrice:\s*"([0-9.]+)"', html)
        if price_js:
            try:
                data["price"] = float(price_js.group(1))
            except ValueError:
                pass

    # 7. Fallback nom depuis og:title
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_title:
            title = og_title.group(1).strip()
            title = re.sub(r'\s*-?\s*JD Sports.*$', '', title, flags=re.IGNORECASE)
            data["name"] = title.strip()

    # 8. Fallback image depuis og:image
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_image:
            data["image"] = og_image.group(1)

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_jdsports_product(url: str) -> DealItem:
    """
    Récupère et parse un produit JD Sports FR.

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

    data = _extract_product_data(resp.text, url)

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

    external_id = data["sku"] or url.split("/")[-2]

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
