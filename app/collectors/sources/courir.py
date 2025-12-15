"""
Collector Courir - Extraction de produits via parsing HTML + JSON-LD.
Version 3: Parse JSON-LD ligne par ligne.
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
    Parse JSON-LD ligne par ligne pour gérer les objets concaténés.
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

    # 1. Parser JSON-LD (peut contenir plusieurs objets sur des lignes séparées)
    for script_match in _JSONLD_RE.finditer(html):
        raw_content = script_match.group(1).strip()
        
        # Parser chaque ligne comme un objet JSON séparé
        for line in raw_content.split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
                
            try:
                jsonld = json.loads(line)
                
                if jsonld.get("@type") == "Product":
                    # Nom du produit
                    if not data["name"]:
                        data["name"] = jsonld.get("name")
                    
                    # Marque
                    if not data["brand"]:
                        brand = jsonld.get("brand")
                        if isinstance(brand, dict):
                            data["brand"] = brand.get("name")
                        elif isinstance(brand, str):
                            data["brand"] = brand
                    
                    # Image
                    if not data["image"]:
                        image = jsonld.get("image")
                        if isinstance(image, list) and image:
                            data["image"] = image[0]
                        elif isinstance(image, str):
                            data["image"] = image
                    
                    # Prix depuis offers
                    if not data["price"]:
                        offers = jsonld.get("offers", {})
                        if isinstance(offers, dict):
                            price = offers.get("price")
                            if price:
                                data["price"] = float(price)
                            data["currency"] = offers.get("priceCurrency", "EUR")
                            
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

    # 2. Chercher discount dans le JSON inline (GTM data)
    discount_match = re.search(r'"discount"\s*:\s*(\d+)', html)
    if discount_match:
        discount = int(discount_match.group(1))
        if discount > 0 and data["price"]:
            data["discount_percent"] = float(discount)
            # Calculer prix original
            data["original_price"] = round(data["price"] / (1 - discount/100), 2)

    # 3. Fallback: meta tags
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).strip()
    
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    # 4. Construire nom complet avec marque si nécessaire
    if data["brand"] and data["name"] and data["brand"].lower() not in data["name"].lower():
        data["name"] = f"{data['brand']} {data['name']}"

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_courir_product(url: str) -> DealItem:
    """
    Récupère et parse un produit Courir.
    Utilise cloudscraper natif pour bypass Cloudflare.
    """
    # Créer scraper sans override de headers
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

    # Mettre à jour l'URL finale après redirection
    final_url = resp.url

    # Vérifier le status HTTP
    if resp.status_code == 403:
        raise BlockedError(
            "Bloqué par protection anti-bot",
            source=SOURCE,
            url=final_url,
            status_code=403,
        )

    if resp.status_code == 404:
        raise DataExtractionError(
            "Produit non trouvé (404)",
            source=SOURCE,
            url=final_url,
        )

    if resp.status_code >= 400:
        raise HTTPError(
            "Erreur HTTP",
            status_code=resp.status_code,
            source=SOURCE,
            url=final_url,
        )

    # Extraire les données
    data = _extract_product_data(resp.text, final_url)

    # Validation
    if not data["name"]:
        raise DataExtractionError(
            "Nom du produit non trouvé",
            source=SOURCE,
            url=final_url,
        )

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(
            f"Prix invalide: {data['price']}",
            field="price",
            source=SOURCE,
            url=final_url,
        )

    # Construire l'external_id
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
