"""
Collector SNS (Sneakersnstuff) - Extraction de produits.
SNS a une protection anti-bot, peut nécessiter Web Unlocker.
"""
import json
import re
from typing import Optional, List

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
from app.services.proxy_service import get_web_unlocker_proxy

SOURCE = "sns"
BASE_URL = "https://www.sneakersnstuff.com"


def _extract_product_id_from_url(url: str) -> Optional[str]:
    """Extrait l'ID produit de l'URL."""
    # URL format: https://www.sneakersnstuff.com/fr/product/12345/brand-model
    match = re.search(r'/product/(\d+)', url)
    if match:
        return match.group(1)
    return None


def _extract_product_data(html: str, url: str) -> dict:
    """Extrait les données produit depuis le HTML."""
    data = {
        "name": None,
        "price": None,
        "original_price": None,
        "discount_percent": None,
        "currency": "EUR",
        "image": None,
        "sku": _extract_product_id_from_url(url),
        "brand": None,
        "sizes": [],
    }
    
    # 1. Parser JSON-LD
    jsonld_matches = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html)
    
    for jsonld_raw in jsonld_matches:
        try:
            jsonld = json.loads(jsonld_raw.strip())
            
            if isinstance(jsonld, list):
                for item in jsonld:
                    if item.get('@type') == 'Product':
                        jsonld = item
                        break
            
            if jsonld.get('@type') == 'Product':
                if not data['name']:
                    data['name'] = jsonld.get('name')
                
                if not data['brand']:
                    brand = jsonld.get('brand')
                    if isinstance(brand, dict):
                        data['brand'] = brand.get('name')
                    elif isinstance(brand, str):
                        data['brand'] = brand
                
                if not data['image']:
                    image = jsonld.get('image')
                    if isinstance(image, list) and image:
                        data['image'] = image[0]
                    elif isinstance(image, str):
                        data['image'] = image
                
                # Prix depuis offers
                offers = jsonld.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                
                if offers.get('price'):
                    data['price'] = float(offers['price'])
                    data['currency'] = offers.get('priceCurrency', 'EUR')
                    
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    
    # 2. Chercher dans les data attributes SNS
    # SNS utilise souvent data-product-info en JSON
    product_info_match = re.search(r'data-product-info=["\']([^"\'>]+)["\']', html)
    if product_info_match:
        try:
            info = json.loads(product_info_match.group(1).replace('&quot;', '"'))
            if not data['price'] and info.get('price'):
                data['price'] = float(info['price'])
            if not data['name'] and info.get('name'):
                data['name'] = info['name']
            if not data['brand'] and info.get('brand'):
                data['brand'] = info['brand']
        except (json.JSONDecodeError, ValueError):
            pass
    
    # 3. Chercher les prix directement dans le HTML
    if not data['price']:
        # Pattern SNS pour les prix
        price_match = re.search(r'class=["\'][^"\'>]*product-price[^"\'>]*["\'][^>]*>\s*([\d.,]+)\s*[€]', html)
        if price_match:
            data['price'] = float(price_match.group(1).replace(',', '.').replace(' ', ''))
        else:
            price_match = re.search(r'([\d]+[.,]?[\d]*)\s*€', html)
            if price_match:
                data['price'] = float(price_match.group(1).replace(',', '.'))
    
    # 4. Prix original et réduction
    was_match = re.search(r'(?:was|from|original)[^>]*>\s*([\d.,]+)\s*€', html, re.IGNORECASE)
    if was_match and data['price']:
        original = float(was_match.group(1).replace(',', '.').replace(' ', ''))
        if original > data['price']:
            data['original_price'] = original
            data['discount_percent'] = round((1 - data['price'] / original) * 100, 1)
    
    # 5. Fallback meta tags
    if not data['name']:
        og_title = re.search(r'<meta property=["\']og:title["\'][^>]*content=["\']([^"\'>]+)["\']', html)
        if og_title:
            data['name'] = og_title.group(1).strip()
    
    if not data['image']:
        og_image = re.search(r'<meta property=["\']og:image["\'][^>]*content=["\']([^"\'>]+)["\']', html)
        if og_image:
            data['image'] = og_image.group(1)
    
    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_sns_product(url: str) -> DealItem:
    """Récupère et parse un produit SNS."""
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )
    
    # Essayer avec Web Unlocker si configuré
    proxy_config = get_web_unlocker_proxy()
    proxies = None
    if proxy_config and proxy_config.get('http'):
        proxies = {
            'http': proxy_config['http'],
            'https': proxy_config.get('https', proxy_config['http']),
        }
    
    try:
        resp = scraper.get(url, timeout=30, allow_redirects=True, proxies=proxies)
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
    
    if not data['name']:
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=final_url)
    if not data['price'] or data['price'] <= 0:
        raise ValidationError(f"Prix invalide: {data['price']}", field="price", source=SOURCE, url=final_url)
    
    external_id = data['sku'] or final_url.split('/')[-1]
    
    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data['name'],
        price=data['price'],
        original_price=data['original_price'],
        discount_percent=data['discount_percent'],
        currency=data['currency'],
        url=final_url,
        image_url=data['image'],
        seller_name=data['brand'],
        brand=data['brand'],
        sizes_available=data['sizes'],
        raw=data,
    )


def discover_sns_products(limit: int = 50) -> List[str]:
    """Découvre les URLs de produits en soldes sur SNS."""
    scraper = cloudscraper.create_scraper()
    urls = []
    
    sale_pages = [
        f"{BASE_URL}/fr/sale/sneakers",
        f"{BASE_URL}/fr/sale/clothing",
    ]
    
    proxy_config = get_web_unlocker_proxy()
    proxies = None
    if proxy_config and proxy_config.get('http'):
        proxies = {
            'http': proxy_config['http'],
            'https': proxy_config.get('https', proxy_config['http']),
        }
    
    for page_url in sale_pages:
        try:
            resp = scraper.get(page_url, timeout=30, proxies=proxies)
            if resp.status_code == 200:
                product_links = re.findall(r'href=["\']([^"\'>]*/product/\d+[^"\'>]*)["\']', resp.text)
                for link in product_links:
                    full_url = link if link.startswith('http') else f"{BASE_URL}{link}"
                    if full_url not in urls:
                        urls.append(full_url)
                        if len(urls) >= limit:
                            return urls
        except Exception:
            continue
    
    return urls
