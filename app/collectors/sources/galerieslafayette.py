"""
Collector Galeries Lafayette - Extraction de produits.
Protection anti-bot forte, nécessite Web Unlocker.
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

SOURCE = "galerieslafayette"
BASE_URL = "https://www.galerieslafayette.com"


def _extract_product_id_from_url(url: str) -> Optional[str]:
    """Extrait l'ID produit de l'URL."""
    # URL format: https://www.galerieslafayette.com/p/brand+model/123456789
    match = re.search(r'/p/[^/]+/(\d+)', url)
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
        "category": None,
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
                
                # Prix
                offers = jsonld.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                
                if offers.get('price'):
                    data['price'] = float(offers['price'])
                    data['currency'] = offers.get('priceCurrency', 'EUR')
                    
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    
    # 2. Chercher dans __NEXT_DATA__ ou window.__PRELOADED_STATE__
    next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', html)
    if next_data_match:
        try:
            next_data = json.loads(next_data_match.group(1))
            # Navigation dans la structure Next.js
            props = next_data.get('props', {}).get('pageProps', {})
            product = props.get('product', {})
            
            if product:
                if not data['name']:
                    data['name'] = product.get('name') or product.get('title')
                if not data['brand']:
                    data['brand'] = product.get('brand', {}).get('name') if isinstance(product.get('brand'), dict) else product.get('brand')
                if not data['price']:
                    price_info = product.get('price', {})
                    if isinstance(price_info, dict):
                        data['price'] = price_info.get('current') or price_info.get('value')
                        data['original_price'] = price_info.get('original') or price_info.get('was')
                    elif isinstance(price_info, (int, float)):
                        data['price'] = float(price_info)
                if not data['image']:
                    images = product.get('images', [])
                    if images:
                        data['image'] = images[0].get('url') if isinstance(images[0], dict) else images[0]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    
    # 3. Fallback: regex pour les prix
    if not data['price']:
        price_match = re.search(r'([\d]+[,.]?[\d]*)\s*€', html)
        if price_match:
            data['price'] = float(price_match.group(1).replace(',', '.').replace(' ', ''))
    
    # 4. Calculer la réduction
    if data['price'] and data['original_price'] and data['original_price'] > data['price']:
        data['discount_percent'] = round((1 - data['price'] / data['original_price']) * 100, 1)
    
    # 5. Meta tags fallback
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
def fetch_galerieslafayette_product(url: str) -> DealItem:
    """Récupère et parse un produit Galeries Lafayette."""
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )
    
    # Web Unlocker pour contourner la protection
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
        raise BlockedError("Bloqué par protection anti-bot - Web Unlocker requis", source=SOURCE, url=final_url, status_code=403)
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
        category=data.get('category'),
        sizes_available=data['sizes'],
        raw=data,
    )


def discover_galerieslafayette_products(limit: int = 50) -> List[str]:
    """Découvre les URLs de produits en soldes sur Galeries Lafayette."""
    scraper = cloudscraper.create_scraper()
    urls = []
    
    sale_pages = [
        f"{BASE_URL}/c/soldes-homme/chaussures-homme",
        f"{BASE_URL}/c/soldes-femme/chaussures-femme",
        f"{BASE_URL}/c/soldes-homme/baskets-sneakers-homme",
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
                product_links = re.findall(r'href=["\']([^"\'>]*/p/[^"\'>]+)["\']', resp.text)
                for link in product_links:
                    full_url = link if link.startswith('http') else f"{BASE_URL}{link}"
                    if full_url not in urls and '/p/' in full_url:
                        urls.append(full_url)
                        if len(urls) >= limit:
                            return urls
        except Exception:
            continue
    
    return urls
