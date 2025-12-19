"""Collector ASOS - Extraction de produits textiles et sneakers.

ASOS - données produit depuis JSON-LD et meta tags.
Utilise Web Unlocker pour bypass Akamai avec stratégies avancées.
"""
import re
import json
import time
import random
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
from app.services.scraping_orchestrator import get_proxy

SOURCE = "asos"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL ASOS."""
    # Format: /fr/homme/product/nike-air-max/prd/12345678
    match = re.search(r'/prd/(\d+)', url)
    return match.group(1) if match else None


def _is_blocked_page(html: str) -> bool:
    """Détecte si la page est une page de blocage/sélection de pays."""
    blocked_indicators = [
        'noindex, nofollow',
        'errorMessage',
        'panel-radios',
        'tabs-list',
        'li-for-panel',
        'country selection',
        'select your country'
    ]
    html_lower = html.lower()
    return any(indicator in html_lower for indicator in blocked_indicators)


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML ASOS."""
    data = {
        "name": None,
        "price": None,
        "original_price": None,
        "discount_percent": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_sku_from_url(url),
        "brand": None,
        "category": "textile",  # ASOS = principalement textile
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
                if isinstance(offers, dict):
                    price = offers.get("price")
                    if price:
                        data["price"] = float(price)
                    data["currency"] = offers.get("priceCurrency", "EUR")
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 2. Prix soldé vs original depuis le state JS
    price_state = re.search(
        r'"current"\s*:\s*\{[^}]*"value"\s*:\s*([0-9.]+)',
        html
    )
    if price_state:
        try:
            data["price"] = float(price_state.group(1))
        except ValueError:
            pass
            
    was_price = re.search(
        r'"previous"\s*:\s*\{[^}]*"value"\s*:\s*([0-9.]+)',
        html
    )
    if was_price:
        try:
            data["original_price"] = float(was_price.group(1))
        except ValueError:
            pass

    # 3. Discount percentage
    discount_match = re.search(r'"discountPercentage"\s*:\s*(\d+)', html)
    if discount_match:
        data["discount_percent"] = float(discount_match.group(1))
    elif data["price"] and data["original_price"] and data["original_price"] > data["price"]:
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

    # 5. Marque depuis le titre
    if not data["brand"] and data["name"]:
        brands = ["Nike", "Adidas", "The North Face", "Tommy Hilfiger", "Lacoste",
                  "Ralph Lauren", "Calvin Klein", "Jack & Jones", "ASOS DESIGN",
                  "New Balance", "Puma", "Reebok", "Jordan", "Carhartt"]
        name_lower = data["name"].lower()
        for brand in brands:
            if brand.lower() in name_lower:
                data["brand"] = brand
                break

    return data


def _get_enhanced_headers() -> dict:
    """Génère des headers plus réalistes pour contourner Akamai."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
    ]
    
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"'
    }


@retry_on_network_errors(retries=3, source=SOURCE)
def fetch_asos_product(url: str) -> DealItem:
    """Récupère et parse un produit ASOS via Web Unlocker avec stratégies avancées."""
    import httpx
    
    # ASOS nécessite Web Unlocker
    proxy_config = get_proxy("web_unlocker")
    
    # Headers rotatifs pour éviter la détection
    headers = _get_enhanced_headers()
    
    # Délai aléatoire pour simuler un comportement humain
    time.sleep(random.uniform(1, 3))
    
    try:
        with httpx.Client(
            timeout=45,  # Timeout plus long pour Akamai
            follow_redirects=True,
            proxy=proxy_config.get("http") if proxy_config else None,
            verify=False if proxy_config else True,
        ) as client:
            
            # Première tentative
            resp = client.get(url, headers=headers)
            
            # Si on détecte une page bloquée, on retry avec des paramètres différents
            if _is_blocked_page(resp.text) and resp.status_code in [200, 422]:
                time.sleep(random.uniform(3, 6))
                headers = _get_enhanced_headers()
                headers["Referer"] = "https://www.asos.com/fr/"
                
                # Deuxième tentative
                resp = client.get(url, headers=headers)
                
                # Si encore bloqué, on essaie une dernière fois avec un délai plus long
                if _is_blocked_page(resp.text):
                    time.sleep(random.uniform(5, 10))
                    headers = _get_enhanced_headers()
                    headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                    resp = client.get(url, headers=headers)
                    
    except httpx.TimeoutException as e:
        raise TimeoutError("Timeout après 45s", source=SOURCE, url=url) from e
    except httpx.ConnectError as e:
        raise NetworkError(f"Erreur de connexion: {e}", source=SOURCE, url=url) from e

    # Vérification si on est toujours sur une page bloquée
    if _is_blocked_page(resp.text):
        raise BlockedError("Page de sélection de pays détectée - Akamai block", source=SOURCE, url=url, status_code=resp.status_code)

    if resp.status_code == 403:
        raise BlockedError("Bloqué par Akamai", source=SOURCE, url=url, status_code=403)

    if resp.status_code == 422:
        raise BlockedError("Requête invalide - possiblement bloqué", source=SOURCE, url=url, status_code=422)

    if resp.status_code == 404:
        raise DataExtractionError("Produit non trouvé", source=SOURCE, url=url)

    if resp.status_code >= 400:
        raise HTTPError("Erreur HTTP", status_code=resp.status_code, source=SOURCE, url=url)

    # Vérification supplémentaire du contenu
    if len(resp.text) < 1000:  # Page trop petite = potentiellement bloquée
        raise DataExtractionError("Contenu de page insuffisant - possiblement bloquée", source=SOURCE, url=url)

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