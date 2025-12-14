"""
Collector Size UK - Extraction de produits avec prix soldés.

Size UK (size.co.uk) - données produit depuis:
- Meta tags et JSON-LD
- Variables JavaScript
- Prix en GBP convertis en EUR
"""
import re
import json
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

SOURCE = "size"
GBP_TO_EUR = 1.17  # Taux approximatif


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL Size."""
    match = re.search(r'/product/[^/]+/(\d+)/?', url)
    if match:
        return match.group(1)
    match = re.search(r'/(\d+)/?$', url.rstrip('/'))
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML."""
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

    # 1. Essayer JSON-LD d'abord
    json_ld = re.search(r'<script type="application/ld\+json"[^>]*>([^<]+)</script>', html)
    if json_ld:
        try:
            ld_data = json.loads(json_ld.group(1))
            if isinstance(ld_data, dict):
                if ld_data.get("@type") == "Product":
                    data["name"] = ld_data.get("name")
                    data["brand"] = ld_data.get("brand", {}).get("name")
                    data["image"] = ld_data.get("image")
                    if "offers" in ld_data:
                        offers = ld_data["offers"]
                        if isinstance(offers, dict):
                            price_gbp = float(offers.get("price", 0))
                            data["price"] = round(price_gbp * GBP_TO_EUR, 2)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 2. Meta og:title pour le nom
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_title:
            title = og_title.group(1).strip()
            title = re.sub(r'\s*[|-]\s*size\?.*$', '', title, flags=re.IGNORECASE)
            data["name"] = title.strip()

    # 3. Prix actuel (sale price) - chercher dans plusieurs endroits
    # JavaScript productPrice
    price_js = re.search(r'productPrice["\']?\s*[:=]\s*["\']?([0-9.]+)', html)
    if price_js:
        try:
            price_gbp = float(price_js.group(1))
            data["price"] = round(price_gbp * GBP_TO_EUR, 2)
        except ValueError:
            pass
    
    # Fallback: chercher dans le HTML
    if not data["price"]:
        price_html = re.search(r'class="[^"]*(?:price|sale|now)[^"]*"[^>]*>.*?([0-9]+[.,]?[0-9]*)', html, re.IGNORECASE | re.DOTALL)
        if price_html:
            try:
                price_str = price_html.group(1).replace(",", ".")
                price_gbp = float(price_str)
                data["price"] = round(price_gbp * GBP_TO_EUR, 2)
            except ValueError:
                pass

    # 4. Prix original (was price / RRP)
    was_price_js = re.search(r'(?:wasPrice|rrpPrice|originalPrice)["\']?\s*[:=]\s*["\']?([0-9.]+)', html, re.IGNORECASE)
    if was_price_js:
        try:
            was_gbp = float(was_price_js.group(1))
            data["original_price"] = round(was_gbp * GBP_TO_EUR, 2)
        except ValueError:
            pass
    
    # Prix barré dans HTML
    if not data["original_price"]:
        was_html = re.search(r'class="[^"]*(?:was|strike|rrp|original)[^"]*"[^>]*>.*?([0-9]+[.,]?[0-9]*)', html, re.IGNORECASE | re.DOTALL)
        if was_html:
            try:
                price_str = was_html.group(1).replace(",", ".")
                was_gbp = float(price_str)
                data["original_price"] = round(was_gbp * GBP_TO_EUR, 2)
            except ValueError:
                pass

    # 5. Calcul du pourcentage de remise
    if data["price"] and data["original_price"] and data["original_price"] > data["price"]:
        data["discount_percent"] = round(
            (1 - data["price"] / data["original_price"]) * 100, 1
        )

    # 6. Image depuis og:image
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_image:
            data["image"] = og_image.group(1)

    # 7. Marque depuis le HTML ou le titre
    if not data["brand"] and data["name"]:
        # Essayer d'extraire la marque du début du titre
        brands = ["Nike", "Adidas", "New Balance", "Jordan", "ASICS", "Puma", "Reebok", 
                  "Vans", "Converse", "UGG", "Timberland", "Salomon", "The North Face"]
        for brand in brands:
            if data["name"].lower().startswith(brand.lower()):
                data["brand"] = brand
                break

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_size_product(url: str) -> DealItem:
    """Récupère et parse un produit Size UK."""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    try:
        resp = scraper.get(url, timeout=30)
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
        raw=data,
    )
