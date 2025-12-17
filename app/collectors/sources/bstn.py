"""
Collector BSTN - Extraction de produits via Shopify JSON API.
BSTN utilise Shopify, donc on peut accéder à l'API /products.json
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

SOURCE = "bstn"
BASE_URL = "https://www.bstn.com"


def _extract_product_id_from_url(url: str) -> Optional[str]:
    """Extrait l'ID produit de l'URL."""
    # URL format: https://www.bstn.com/eu_fr/p/brand-model-123456
    match = re.search(r'/p/([^/\?]+)', url)
    if match:
        return match.group(1)
    return None


def _parse_shopify_product(product: dict, variant: dict = None) -> dict:
    """Parse un produit Shopify."""
    # Prendre le premier variant si non spécifié
    if not variant and product.get('variants'):
        variant = product['variants'][0]
    
    price = None
    original_price = None
    discount_percent = None
    
    if variant:
        price = float(variant.get('price', 0))
        compare_price = variant.get('compare_at_price')
        if compare_price:
            original_price = float(compare_price)
            if original_price > price:
                discount_percent = round((1 - price / original_price) * 100, 1)
    
    # Image
    image_url = None
    if product.get('images') and len(product['images']) > 0:
        image_url = product['images'][0].get('src')
    elif product.get('image'):
        image_url = product['image'].get('src')
    
    # Extraire la marque du titre ou vendor
    brand = product.get('vendor', '')
    title = product.get('title', '')
    
    # Sizes disponibles
    sizes = []
    for v in product.get('variants', []):
        if v.get('available', True):
            size = v.get('option1') or v.get('title')
            if size and size not in sizes:
                sizes.append(size)
    
    return {
        'id': str(product.get('id', '')),
        'handle': product.get('handle', ''),
        'title': title,
        'brand': brand,
        'price': price,
        'original_price': original_price,
        'discount_percent': discount_percent,
        'image_url': image_url,
        'sizes': sizes,
        'product_type': product.get('product_type', ''),
    }


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_bstn_product(url: str) -> DealItem:
    """
    Récupère et parse un produit BSTN.
    Utilise l'API Shopify JSON si possible.
    """
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )
    
    # Essayer d'abord l'API JSON Shopify
    product_handle = _extract_product_id_from_url(url)
    
    if product_handle:
        # Tenter l'API JSON
        json_url = f"{BASE_URL}/products/{product_handle}.json"
        try:
            resp = scraper.get(json_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if 'product' in data:
                    product_data = _parse_shopify_product(data['product'])
                    
                    return DealItem(
                        source=SOURCE,
                        external_id=product_data['id'] or product_handle,
                        title=f"{product_data['brand']} {product_data['title']}".strip(),
                        price=product_data['price'],
                        original_price=product_data['original_price'],
                        discount_percent=product_data['discount_percent'],
                        currency="EUR",
                        url=url,
                        image_url=product_data['image_url'],
                        seller_name=product_data['brand'],
                        brand=product_data['brand'],
                        sizes_available=product_data['sizes'],
                        category=product_data['product_type'],
                        raw=data['product'],
                    )
        except (json.JSONDecodeError, KeyError):
            pass  # Fallback to HTML parsing
    
    # Fallback: Parser le HTML
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
    
    # Parser JSON-LD ou meta tags
    html = resp.text
    
    # Chercher JSON-LD Product
    jsonld_match = re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', html)
    data = {}
    
    if jsonld_match:
        try:
            jsonld = json.loads(jsonld_match.group(1))
            if isinstance(jsonld, list):
                for item in jsonld:
                    if item.get('@type') == 'Product':
                        jsonld = item
                        break
            
            if jsonld.get('@type') == 'Product':
                data['name'] = jsonld.get('name')
                data['brand'] = jsonld.get('brand', {}).get('name') if isinstance(jsonld.get('brand'), dict) else jsonld.get('brand')
                data['image'] = jsonld.get('image', [None])[0] if isinstance(jsonld.get('image'), list) else jsonld.get('image')
                
                offers = jsonld.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                data['price'] = float(offers.get('price', 0))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    
    # Fallback meta tags
    if not data.get('name'):
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data['name'] = og_title.group(1)
    
    if not data.get('image'):
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data['image'] = og_image.group(1)
    
    if not data.get('price'):
        price_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        if price_match:
            data['price'] = float(price_match.group(1))
    
    if not data.get('name'):
        raise DataExtractionError("Nom du produit non trouvé", source=SOURCE, url=final_url)
    if not data.get('price') or data['price'] <= 0:
        raise ValidationError(f"Prix invalide: {data.get('price')}", field="price", source=SOURCE, url=final_url)
    
    external_id = product_handle or final_url.split('/')[-1]
    
    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data['name'],
        price=data['price'],
        original_price=data.get('original_price'),
        discount_percent=data.get('discount_percent'),
        currency="EUR",
        url=final_url,
        image_url=data.get('image'),
        seller_name=data.get('brand'),
        brand=data.get('brand'),
        raw=data,
    )


def discover_bstn_products(limit: int = 50) -> List[str]:
    """Découvre les URLs de produits en soldes sur BSTN."""
    scraper = cloudscraper.create_scraper()
    urls = []
    
    # Pages de soldes
    sale_pages = [
        f"{BASE_URL}/eu_fr/sale/sneakers",
        f"{BASE_URL}/eu_fr/sale/apparel",
    ]
    
    for page_url in sale_pages:
        try:
            resp = scraper.get(page_url, timeout=30)
            if resp.status_code == 200:
                # Extraire les liens produits
                product_links = re.findall(r'href="(/eu_fr/p/[^"]+)"', resp.text)
                for link in product_links:
                    full_url = f"{BASE_URL}{link}"
                    if full_url not in urls:
                        urls.append(full_url)
                        if len(urls) >= limit:
                            return urls
        except Exception:
            continue
    
    return urls
