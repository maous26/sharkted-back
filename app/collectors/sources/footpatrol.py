"""
Collector Footpatrol - Extraction de sneakers premium UK.

Footpatrol (footpatrol.com) - données depuis JSON-LD et meta tags.
"""
import re
import json
from typing import Optional

import cloudscraper
from app.utils.http_stealth import create_stealth_scraper, get_stealth_headers
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

SOURCE = "footpatrol"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL Footpatrol."""
    # Format: /product/nike-air-max-1/12345678.html
    match = re.search(r'/(\d+)\.html', url)
    if match:
        return match.group(1)
    match = re.search(r'/product/[^/]+/([^/]+)', url)
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML Footpatrol."""
    data = {
        "name": None,
        "price": None,
        "original_price": None,
        "discount_percent": None,
        "currency": "GBP",  # UK store
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
        "category": "sneakers",
    }

    # 1. JSON-LD Product
    json_ld_match = re.search(
        r'<script type="application/ld\+json"[^>]*>([^<]+)</script>',
        html
    )
    if json_ld_match:
        try:
            ld_data = json.loads(json_ld_match.group(1))
            if isinstance(ld_data, dict) and ld_data.get("@type") == "Product":
                data["name"] = ld_data.get("name")
                data["brand"] = ld_data.get("brand", {}).get("name")
                
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
                    data["currency"] = offers.get("priceCurrency", "GBP")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 2. Prix depuis data attributes
    price_data = re.search(r'data-price="([0-9.]+)"', html)
    if price_data and not data["price"]:
        data["price"] = float(price_data.group(1))
        
    # 3. Prix original (was price)
    was_price = re.search(r'was[^>]*>\s*[^0-9]*([0-9,.]+)', html, re.IGNORECASE)
    if was_price:
        try:
            price_str = was_price.group(1).replace(",", ".").replace(" ", "")
            data["original_price"] = float(price_str)
        except ValueError:
            pass

    # Alternative: RRP
    if not data["original_price"]:
        rrp = re.search(r'rrp[^>]*>\s*[^0-9]*([0-9,.]+)', html, re.IGNORECASE)
        if rrp:
            try:
                data["original_price"] = float(rrp.group(1).replace(",", "."))
            except ValueError:
                pass

    # 4. Calcul discount
    if data["price"] and data["original_price"] and data["original_price"] > data["price"]:
        data["discount_percent"] = round(
            (1 - data["price"] / data["original_price"]) * 100, 1
        )

    # 5. Fallback meta tags
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).split(" | ")[0].strip()
            
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    # 6. Marque depuis le titre
    if not data["brand"] and data["name"]:
        brands = ["Nike", "Adidas", "New Balance", "Asics", "Jordan", 
                  "Puma", "Reebok", "Converse", "Vans", "Salomon"]
        for brand in brands:
            if brand.lower() in data["name"].lower():
                data["brand"] = brand
                break

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_footpatrol_product(url: str) -> DealItem:
    """Récupère et parse un produit Footpatrol."""
    scraper, headers = create_stealth_scraper("footpatrol")

    try:
        resp = scraper.get(url, headers=headers, timeout=30)
    except requests.exceptions.Timeout as e:
        raise TimeoutError("Timeout après 30s", source=SOURCE, url=url) from e
    except requests.exceptions.ConnectionError as e:
        raise NetworkError(f"Erreur de connexion: {e}", source=SOURCE, url=url) from e
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Erreur réseau: {e}", source=SOURCE, url=url) from e

    if resp.status_code == 403:
        raise BlockedError("Bloqué par protection anti-bot", source=SOURCE, url=url, status_code=403)

    if resp.status_code == 404:
        raise DataExtractionError("Produit non trouvé (404)", source=SOURCE, url=url)

    if resp.status_code >= 400:
        raise HTTPError("Erreur HTTP", status_code=resp.status_code, source=SOURCE, url=url)

    data = _extract_product_data(resp.text, url)

    if not data["name"]:
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=url)

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(f"Prix invalide: {data['price']}", field="price", source=SOURCE, url=url)

    external_id = data["sku"] or url.split("/")[-1].replace(".html", "")

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
