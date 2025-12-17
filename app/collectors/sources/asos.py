"""
Collector ASOS - Extraction via API JSON (robuste et économique).

Utilise l'API de recherche ASOS pour trouver directement les produits en promotion
avec leurs prix actuels et précédents.
"""
import re
from typing import Optional, List

import requests

from app.normalizers.item import DealItem
from app.core.exceptions import DataExtractionError, NetworkError, ValidationError
from app.services.proxy_service import get_web_unlocker_proxy

SOURCE = "asos"
BASE_URL = "https://www.asos.com/fr"
API_SEARCH = "https://www.asos.com/api/product/search/v2/"

# Recherches pour trouver des deals sneakers
SEARCH_QUERIES = [
    "sneakers promo",
    "baskets soldes",
    "nike promo",
    "adidas soldes",
    "new balance promo",
]


def fetch_asos_product(url: str) -> DealItem:
    """
    Récupère un produit ASOS depuis son URL.
    Utilise le parsing HTML pour les détails.
    """
    proxy = get_web_unlocker_proxy()
    
    id_match = re.search(r'/prd/([0-9]+)', url)
    if not id_match:
        raise ValidationError("ID produit non trouvé dans l'URL", field="url", source=SOURCE, url=url)
    
    product_id = id_match.group(1)
    
    try:
        resp = requests.get(url, proxies=proxy, timeout=60, verify=False)
        if resp.status_code != 200:
            raise NetworkError(f"HTTP {resp.status_code}", source=SOURCE, url=url)
        
        # Titre
        title_match = re.search(r'<title>([^<]+)</title>', resp.text)
        title = title_match.group(1).split(' | ')[0].strip() if title_match else None
        
        if not title:
            raise DataExtractionError("Titre non trouvé", source=SOURCE, url=url)
        
        # Prix depuis productPrice
        price = None
        original_price = None
        
        price_match = re.search(
            r'"productPrice"\s*:\s*\{\s*"current"\s*:\s*\{\s*"value"\s*:\s*([0-9.]+)',
            resp.text
        )
        if price_match:
            price = float(price_match.group(1))
        
        prev_match = re.search(
            r'"previous"\s*:\s*\{\s*"value"\s*:\s*([0-9.]+)',
            resp.text
        )
        if prev_match:
            original_price = float(prev_match.group(1))
        
        if not price:
            raise ValidationError("Prix non trouvé", field="price", source=SOURCE, url=url)
        
        # Discount
        discount_percent = None
        if original_price and original_price > price:
            discount_percent = round((1 - price / original_price) * 100, 1)
        
        # Brand depuis JSON-LD
        import json
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        image_url = None
        brand = None
        
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if 'image' in data:
                        image_url = data['image']
                    if 'brand' in data:
                        brand_data = data['brand']
                        if isinstance(brand_data, dict):
                            brand = brand_data.get('name')
            except:
                pass
        
        if not brand:
            brand = "ASOS"
        
        return DealItem(
            source=SOURCE,
            external_id=product_id,
            title=title,
            price=price,
            original_price=original_price,
            discount_percent=discount_percent,
            currency="EUR",
            url=url,
            image_url=image_url,
            brand=brand,
            seller_name="ASOS",
        )
        
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Erreur réseau: {e}", source=SOURCE, url=url)


def discover_asos_products(limit: int = 50) -> List[str]:
    """
    Découvre les URLs de produits en utilisant l'API de recherche ASOS.
    Priorise les produits en soldes via des recherches ciblées.
    """
    proxy = get_web_unlocker_proxy()
    urls = []
    seen_ids = set()
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    
    for query in SEARCH_QUERIES:
        if len(urls) >= limit:
            break
            
        try:
            params = {
                "q": query,
                "offset": 0,
                "limit": 72,
                "store": "FR",
                "lang": "fr-FR", 
                "currency": "EUR",
                "country": "FR",
            }
            
            resp = requests.get(
                API_SEARCH, 
                params=params, 
                headers=headers, 
                proxies=proxy, 
                timeout=60, 
                verify=False
            )
            
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            products = data.get("products", [])
            
            # Prioriser les produits en soldes
            sale_products = [p for p in products if p.get("price", {}).get("isMarkedDown")]
            
            for product in sale_products:
                if len(urls) >= limit:
                    break
                    
                product_id = product.get("id")
                if product_id in seen_ids:
                    continue
                    
                seen_ids.add(product_id)
                product_url = product.get("url", "")
                
                if product_id and product_url:
                    full_url = f"{BASE_URL}/{product_url}"
                    urls.append(full_url)
                        
        except Exception as e:
            print(f"Error discovering ASOS products with query '{query}': {e}")
    
    return urls[:limit]


def fetch_asos_products_batch(limit: int = 50) -> List[DealItem]:
    """
    Récupère les produits ASOS directement depuis l'API de recherche.
    Plus efficace que de scraper chaque page individuelle.
    Retourne directement les DealItems avec les vraies données de prix.
    """
    proxy = get_web_unlocker_proxy()
    items = []
    seen_ids = set()
    
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    
    for query in SEARCH_QUERIES:
        if len(items) >= limit:
            break
            
        try:
            params = {
                "q": query,
                "offset": 0,
                "limit": 72,
                "store": "FR",
                "lang": "fr-FR",
                "currency": "EUR",
                "country": "FR",
            }
            
            resp = requests.get(
                API_SEARCH,
                params=params,
                headers=headers,
                proxies=proxy,
                timeout=60,
                verify=False
            )
            
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            products = data.get("products", [])
            
            # Seulement les produits soldés
            sale_products = [p for p in products if p.get("price", {}).get("isMarkedDown")]
            
            for product in sale_products:
                if len(items) >= limit:
                    break
                    
                try:
                    product_id = product.get("id")
                    if product_id in seen_ids:
                        continue
                    
                    seen_ids.add(product_id)
                    
                    name = product.get("name", "")
                    brand_name = product.get("brandName", "ASOS")
                    price_data = product.get("price", {})
                    
                    current_price = price_data.get("current", {}).get("value")
                    previous_price = price_data.get("previous", {}).get("value")
                    
                    if not product_id or not current_price:
                        continue
                    
                    # Calculer discount
                    discount_percent = None
                    if previous_price and previous_price > current_price:
                        discount_percent = round((1 - current_price / previous_price) * 100, 1)
                    
                    # URL
                    url_path = product.get("url", f"prd/{product_id}")
                    full_url = f"{BASE_URL}/{url_path}"
                    
                    # Image
                    image_url = product.get("imageUrl")
                    if image_url and not image_url.startswith("http"):
                        image_url = f"https://{image_url}"
                    
                    item = DealItem(
                        source=SOURCE,
                        external_id=str(product_id),
                        title=name,
                        price=current_price,
                        original_price=previous_price,
                        discount_percent=discount_percent,
                        currency="EUR",
                        url=full_url,
                        image_url=image_url,
                        brand=brand_name,
                        seller_name="ASOS",
                    )
                    items.append(item)
                    
                except Exception:
                    continue
                    
        except Exception as e:
            print(f"Error fetching ASOS products with query '{query}': {e}")
    
    return items[:limit]
