"""
Collector La Redoute - Extraction de produits textiles.

La Redoute - données produit depuis JSON-LD et meta tags.
Utilise Web Unlocker pour bypass protection anti-bot.
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

SOURCE = "laredoute"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL La Redoute."""
    # Format: /ppdp/prod-123456789.aspx ou /prp/...
    match = re.search(r'prod-([A-Za-z0-9]+)', url)
    return match.group(1) if match else None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML La Redoute."""
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

    # 1. JSON-LD Product
    json_ld_matches = re.findall(
        r'<script type="application/ld\+json"[^>]*>([\s\S]*?)</script>',
        html
    )
    for match in json_ld_matches:
        try:
            ld_data = json.loads(match.strip())
            if isinstance(ld_data, dict) and ld_data.get("@type") == "Product":
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
                if isinstance(offers, dict):
                    price = offers.get("price")
                    if price:
                        data["price"] = float(price)
                elif isinstance(offers, list) and offers:
                    price = offers[0].get("price")
                    if price:
                        data["price"] = float(price)
                break
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # 2. Prix soldé et original depuis le HTML
    # Prix actuel
    price_match = re.search(
        r'class="[^"]*price[^"]*current[^"]*"[^>]*>\s*([0-9]+[,.]?[0-9]*)\s*[€EUR]',
        html, re.IGNORECASE
    )
    if price_match:
        try:
            data["price"] = float(price_match.group(1).replace(",", "."))
        except ValueError:
            pass

    # Prix barré
    was_match = re.search(
        r'class="[^"]*(?:strike|crossed|was|ancien)[^"]*"[^>]*>\s*([0-9]+[,.]?[0-9]*)\s*[€EUR]',
        html, re.IGNORECASE
    )
    if was_match:
        try:
            data["original_price"] = float(was_match.group(1).replace(",", "."))
        except ValueError:
            pass

    # 3. Discount - Only from price comparison, not from promo text
    # Don't capture generic "-50%" which may be conditional promos
    if data["price"] and data["original_price"] and data["original_price"] > data["price"]:
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

    # 5. Marque depuis le titre ou HTML
    if not data["brand"] and data["name"]:
        brands = ["Lacoste", "Tommy Hilfiger", "Ralph Lauren", "Nike", "Adidas",
                  "The North Face", "Calvin Klein", "Levi's", "Guess", "Kaporal",
                  "Schott", "Superdry", "Scotch & Soda", "POLO RALPH LAUREN",
                  "NAPAPIJRI", "BARBOUR", "GANT", "HACKETT"]
        name_lower = data["name"].lower()
        for brand in brands:
            if brand.lower() in name_lower:
                data["brand"] = brand
                break

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_laredoute_product(url: str) -> DealItem:
    """Récupère et parse un produit La Redoute via Web Unlocker."""
    # La Redoute nécessite Web Unlocker pour bypass protection
    proxy_config = get_proxy("web_unlocker")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
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
        raise BlockedError("Bloqué par protection anti-bot", source=SOURCE, url=url, status_code=403)

    if resp.status_code == 404:
        raise DataExtractionError("Produit non trouvé", source=SOURCE, url=url)

    if resp.status_code >= 400:
        raise HTTPError("Erreur HTTP", status_code=resp.status_code, source=SOURCE, url=url)

    data = _extract_product_data(resp.text, url)

    if not data["name"]:
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=url)

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(f"Prix invalide: {data['price']}", field="price", source=SOURCE, url=url)

    external_id = data["sku"] or url.split("/")[-1].replace(".aspx", "")

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
