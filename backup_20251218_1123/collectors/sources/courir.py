"""
Collector Courir - Extraction de produits via parsing HTML + JSON-LD.
Version 4: Extraction améliorée des prix.
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
    """Extrait le SKU de l'URL."""
    match = re.search(r'-(\d{6,})\.html', url)
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """
    Extrait les données produit depuis le HTML.
    Cherche dans plusieurs sources: JSON-LD, GTM data, HTML.
    """
    data = {
        "name": None,
        "price": None,
        "original_price": None,
        "discount_percent": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
    }

    # 1. Parser JSON-LD
    for script_match in _JSONLD_RE.finditer(html):
        raw_content = script_match.group(1).strip()
        
        for line in raw_content.split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
                
            try:
                jsonld = json.loads(line)
                
                if jsonld.get("@type") == "Product":
                    if not data["name"]:
                        data["name"] = jsonld.get("name")
                    
                    if not data["brand"]:
                        brand = jsonld.get("brand")
                        if isinstance(brand, dict):
                            data["brand"] = brand.get("name")
                        elif isinstance(brand, str):
                            data["brand"] = brand
                    
                    if not data["image"]:
                        image = jsonld.get("image")
                        if isinstance(image, list) and image:
                            data["image"] = image[0]
                        elif isinstance(image, str):
                            data["image"] = image
                    
                    # Prix depuis offers
                    offers = jsonld.get("offers", {})
                    if isinstance(offers, dict):
                        price = offers.get("price")
                        if price and not data["price"]:
                            data["price"] = float(price)
                        data["currency"] = offers.get("priceCurrency", "EUR")
                            
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    # 2. Chercher les données GTM/dataLayer pour discount et prix
    # Pattern: "discount":30, "originalPrice":150, "salePrice":105
    discount_match = re.search(r'"discount"\s*:\s*(\d+)', html)
    if discount_match:
        discount = int(discount_match.group(1))
        if discount > 0:
            data["discount_percent"] = float(discount)
    
    # Prix original depuis GTM
    original_patterns = [
        r'"originalPrice"\s*:\s*([\d.]+)',
        r'"original_price"\s*:\s*([\d.]+)',
        r'"basePrice"\s*:\s*([\d.]+)',
        r'"regularPrice"\s*:\s*([\d.]+)',
        r'"was"\s*:\s*([\d.]+)',
    ]
    for pattern in original_patterns:
        match = re.search(pattern, html)
        if match:
            try:
                orig = float(match.group(1))
                if orig > 0 and (not data["original_price"] or orig > data["original_price"]):
                    data["original_price"] = orig
                    break
            except ValueError:
                continue

    # Prix soldé depuis GTM
    sale_patterns = [
        r'"salePrice"\s*:\s*([\d.]+)',
        r'"sale_price"\s*:\s*([\d.]+)',
        r'"finalPrice"\s*:\s*([\d.]+)',
        r'"now"\s*:\s*([\d.]+)',
    ]
    for pattern in sale_patterns:
        match = re.search(pattern, html)
        if match:
            try:
                sale = float(match.group(1))
                if sale > 0:
                    data["price"] = sale
                    break
            except ValueError:
                continue

    # 3. Chercher dans le HTML les prix barrés
    # Prix barré (original)
    was_price_html = re.search(
        r'class="[^"]*(?:was|strike|crossed|old|regular)[^"]*"[^>]*>\s*([\d,]+(?:[.,]\d{2})?)\s*[€EUR]',
        html, re.IGNORECASE
    )
    if was_price_html and not data["original_price"]:
        try:
            price_str = was_price_html.group(1).replace(",", ".").replace(" ", "")
            data["original_price"] = float(price_str)
        except ValueError:
            pass

    # Prix actuel
    now_price_html = re.search(
        r'class="[^"]*(?:price|now|sale|final|current)[^"]*"[^>]*>\s*([\d,]+(?:[.,]\d{2})?)\s*[€EUR]',
        html, re.IGNORECASE
    )
    if now_price_html and not data["price"]:
        try:
            price_str = now_price_html.group(1).replace(",", ".").replace(" ", "")
            data["price"] = float(price_str)
        except ValueError:
            pass

    # 4. Calculer les valeurs manquantes
    if data["price"] and data["original_price"] and not data["discount_percent"]:
        if data["original_price"] > data["price"]:
            data["discount_percent"] = round((1 - data["price"] / data["original_price"]) * 100, 1)
    
    if data["price"] and data["discount_percent"] and not data["original_price"]:
        if data["discount_percent"] > 0:
            data["original_price"] = round(data["price"] / (1 - data["discount_percent"] / 100), 2)

    # 5. Fallback meta tags
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).strip()
    
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    # 6. Construire nom complet avec marque
    if data["brand"] and data["name"] and data["brand"].lower() not in data["name"].lower():
        data["name"] = f"{data['brand']} {data['name']}"

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_courir_product(url: str) -> DealItem:
    """Récupère et parse un produit Courir."""
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )

    try:
        resp = scraper.get(url, timeout=30, allow_redirects=True)
    except requests.exceptions.Timeout as e:
        raise TimeoutError("Timeout après 30s", source=SOURCE, url=url) from e
    except requests.exceptions.ConnectionError as e:
        raise NetworkError(f"Erreur de connexion: {e}", source=SOURCE, url=url) from e
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Erreur réseau: {e}", source=SOURCE, url=url) from e

    final_url = resp.url

    if resp.status_code == 403:
        raise BlockedError("Bloqué par protection anti-bot", source=SOURCE, url=final_url, status_code=403)
    if resp.status_code == 404:
        raise DataExtractionError("Produit non trouvé (404)", source=SOURCE, url=final_url)
    if resp.status_code >= 400:
        raise HTTPError("Erreur HTTP", status_code=resp.status_code, source=SOURCE, url=final_url)

    data = _extract_product_data(resp.text, final_url)

    if not data["name"]:
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=final_url)
    if not data["price"] or data["price"] <= 0:
        raise ValidationError(f"Prix invalide: {data['price']}", field="price", source=SOURCE, url=final_url)

    external_id = data["sku"] or final_url.split("/")[-1].replace(".html", "")

    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data["name"],
        price=data["price"],
        original_price=data.get("original_price"),
        discount_percent=data.get("discount_percent"),
        currency=data["currency"],
        url=final_url,
        image_url=data["image"],
        seller_name=data["brand"],
        brand=data["brand"],
        raw=data,
    )
