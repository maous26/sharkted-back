"""
Collector Printemps - Extraction de textile premium.

Printemps (printemps.com) - données depuis JSON-LD (Product ou ProductGroup).
Utilise Web Unlocker pour bypass protection.
"""
import re
import json
from typing import Optional

import httpx
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
from app.services.scraping_orchestrator import get_proxy

SOURCE = "printemps"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL Printemps."""
    # Format: /fr/fr/brand-product-name-XXXXXXX (7-8 digits at end)
    match = re.search(r'-(\d{6,8})(?:\?|$)', url)
    if match:
        return match.group(1)
    return None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML Printemps."""
    data = {
        "name": None,
        "price": None,
        "original_price": None,
        "discount_percent": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
        "category": "textile",
    }

    # 1. JSON-LD Product or ProductGroup
    json_ld_matches = re.findall(
        r'<script type="application/ld\+json"[^>]*>([\s\S]*?)</script>',
        html
    )
    for json_str in json_ld_matches:
        try:
            ld_data = json.loads(json_str)
            ld_type = ld_data.get("@type") if isinstance(ld_data, dict) else None

            # Handle ProductGroup (Printemps uses this for products with variants)
            if ld_type == "ProductGroup":
                data["name"] = ld_data.get("name")

                brand = ld_data.get("brand")
                if isinstance(brand, dict):
                    data["brand"] = brand.get("name")
                elif isinstance(brand, str):
                    data["brand"] = brand

                image = ld_data.get("image")
                if isinstance(image, list) and image:
                    data["image"] = image[0]
                elif isinstance(image, str):
                    data["image"] = image

                # Get price from first variant
                variants = ld_data.get("hasVariant", [])
                if variants:
                    first_variant = variants[0]
                    offers = first_variant.get("offers", {})
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        price = offers.get("price")
                        if price:
                            data["price"] = float(price)
                        data["currency"] = offers.get("priceCurrency", "EUR")

                # Also check ProductGroup level offers for price range
                pg_offers = ld_data.get("offers", {})
                if isinstance(pg_offers, dict) and not data["price"]:
                    low_price = pg_offers.get("lowPrice")
                    if low_price:
                        data["price"] = float(low_price)

                break  # Found ProductGroup, stop

            # Handle standard Product
            elif ld_type == "Product":
                data["name"] = ld_data.get("name")

                brand = ld_data.get("brand")
                if isinstance(brand, dict):
                    data["brand"] = brand.get("name")
                elif isinstance(brand, str):
                    data["brand"] = brand

                image = ld_data.get("image")
                if isinstance(image, list) and image:
                    data["image"] = image[0]
                elif isinstance(image, str):
                    data["image"] = image

                offers = ld_data.get("offers", {})
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    price = offers.get("price")
                    if price:
                        data["price"] = float(price)
                    data["currency"] = offers.get("priceCurrency", "EUR")
                break

        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # 2. Prix barré (original) - Printemps format: striked">105,00€
    striked_patterns = [
        r'striked"[^>]*>([0-9]+[,.]?[0-9]*)',  # striked">105,00
        r'class="[^"]*strike[^"]*"[^>]*>([0-9]+[,.]?[0-9]*)',  # class with strike
        r'(?:crossed|original|was|avant)[^>]*>[^0-9]*([0-9]+[,.]?[0-9]*)',
    ]
    for pattern in striked_patterns:
        crossed_price = re.search(pattern, html, re.IGNORECASE)
        if crossed_price:
            try:
                price_str = crossed_price.group(1).replace(",", ".")
                orig_price = float(price_str)
                if orig_price > (data["price"] or 0):
                    data["original_price"] = orig_price
                    break
            except ValueError:
                continue

    # 3. Discount from HTML - Printemps format: price--ratio">-30%
    discount_patterns = [
        r'price--ratio"[^>]*>-(\d+)%',  # Printemps specific
        r'-(\d+)\s*%',  # Generic
    ]
    for pattern in discount_patterns:
        discount_match = re.search(pattern, html)
        if discount_match:
            data["discount_percent"] = float(discount_match.group(1))
            break

    # Calculate discount if we have both prices but no explicit discount
    if not data["discount_percent"] and data["price"] and data["original_price"] and data["original_price"] > data["price"]:
        data["discount_percent"] = round(
            (1 - data["price"] / data["original_price"]) * 100, 1
        )

    # 4. Fallback meta tags
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).split(" | ")[0].strip()

    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    # 5. Catégorie depuis l'URL
    if "/chaussures/" in url or "/sneakers/" in url or "/baskets/" in url:
        data["category"] = "sneakers"

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_printemps_product(url: str) -> DealItem:
    """Récupère et parse un produit Printemps via Web Unlocker."""
    # Printemps nécessite Web Unlocker
    proxy_config = get_proxy("web_unlocker")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    try:
        with httpx.Client(
            timeout=30,
            follow_redirects=True,
            proxy=proxy_config.get("http") if proxy_config else None,
            verify=False if proxy_config else True,
        ) as client:
            resp = client.get(url, headers=headers)
    except httpx.TimeoutException as e:
        raise TimeoutError("Timeout après 30s", source=SOURCE, url=url) from e
    except httpx.ConnectError as e:
        raise NetworkError(f"Erreur de connexion: {e}", source=SOURCE, url=url) from e

    if resp.status_code == 403:
        raise BlockedError("Bloqué par protection", source=SOURCE, url=url, status_code=403)

    if resp.status_code == 404:
        raise DataExtractionError("Produit non trouvé", source=SOURCE, url=url)

    if resp.status_code >= 400:
        raise HTTPError("Erreur HTTP", status_code=resp.status_code, source=SOURCE, url=url)

    data = _extract_product_data(resp.text, url)

    if not data["name"]:
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=url)

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(f"Prix invalide: {data['price']}", field="price", source=SOURCE, url=url)

    external_id = data["sku"] or url.split("/")[-1]

    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data["name"],
        price=data["price"],
        original_price=data["original_price"],
        discount_percent=data["discount_percent"],
        currency=data["currency"],
        url=url,
        image_url=data["image"],
        seller_name=data["brand"],
        brand=data["brand"],
        category=data["category"],
        raw=data,
    )
