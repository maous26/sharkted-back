"""
Collector Footpatrol - Extraction via API Shopify JSON.
Footpatrol utilise Shopify, donc accès direct à l'API /products.json
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

SOURCE = "footpatrol"
BASE_URL = "https://www.footpatrol.com"


def _parse_shopify_product(product: dict, variant: dict = None) -> dict:
    """Parse un produit Shopify."""
    if not variant and product.get('variants'):
        variant = product['variants'][0]
    
    price = None
    original_price = None
    discount_percent = None
    
    if variant:
        price_str = variant.get('price', '0')
        price = float(price_str) if price_str else 0
        compare_price = variant.get('compare_at_price')
        if compare_price:
            original_price = float(compare_price)
            if original_price > price and price > 0:
                discount_percent = round((1 - price / original_price) * 100, 1)
    
    # Image
    image_url = None
    if product.get('images') and len(product['images']) > 0:
        image_url = product['images'][0].get('src')
    elif product.get('image'):
        image_url = product['image'].get('src')
    
    brand = product.get('vendor', '')
    title = product.get('title', '')
    handle = product.get('handle', '')
    
    # Sizes disponibles
    sizes = []
    for v in product.get('variants', []):
        if v.get('available', True):
            size = v.get('option1') or v.get('title')
            if size and size not in sizes:
                sizes.append(size)
    
    return {
        'id': str(product.get('id', '')),
        'handle': handle,
        'title': title,
        'brand': brand,
        'price': price,
        'original_price': original_price,
        'discount_percent': discount_percent,
        'image_url': image_url,
        'sizes': sizes,
        'product_type': product.get('product_type', ''),
        'url': f"{BASE_URL}/products/{handle}",
    }


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_footpatrol_product(url: str) -> DealItem:
    """Récupère et parse un produit Footpatrol via API Shopify."""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    
    # Extraire le handle du produit
    handle_match = re.search(r'/products/([^/\?]+)', url)
    if not handle_match:
        handle_match = re.search(r'/([^/]+)$', url)
    
    if handle_match:
        handle = handle_match.group(1)
        json_url = f"{BASE_URL}/products/{handle}.json"
        
        try:
            resp = scraper.get(json_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if 'product' in data:
                    product_data = _parse_shopify_product(data['product'])
                    
                    if not product_data['price'] or product_data['price'] <= 0:
                        raise ValidationError(f"Prix invalide: {product_data['price']}", field="price", source=SOURCE, url=url)
                    
                    full_title = f"{product_data['brand']} {product_data['title']}".strip()
                    
                    return DealItem(
                        source=SOURCE,
                        external_id=product_data['id'] or handle,
                        title=full_title,
                        price=product_data['price'],
                        original_price=product_data['original_price'],
                        discount_percent=product_data['discount_percent'],
                        currency="GBP",
                        url=product_data['url'],
                        image_url=product_data['image_url'],
                        seller_name=product_data['brand'],
                        brand=product_data['brand'],
                        sizes_available=product_data['sizes'],
                        category=product_data['product_type'],
                        raw=data['product'],
                    )
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Erreur réseau: {e}", source=SOURCE, url=url) from e
        except json.JSONDecodeError:
            pass
    
    raise DataExtractionError("Impossible de récupérer le produit", source=SOURCE, url=url)


def discover_footpatrol_products(limit: int = 50) -> List[str]:
    """Découvre les URLs de produits en soldes sur Footpatrol via API Shopify."""
    scraper = cloudscraper.create_scraper()
    urls = []
    
    # API Shopify - collections sale
    api_url = f"{BASE_URL}/collections/sale/products.json?limit={min(limit, 250)}"
    
    try:
        resp = scraper.get(api_url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            for product in data.get('products', []):
                handle = product.get('handle')
                if handle:
                    product_url = f"{BASE_URL}/products/{handle}"
                    urls.append(product_url)
                    if len(urls) >= limit:
                        break
    except Exception as e:
        print(f"Error discovering footpatrol products: {e}")
    
    return urls
