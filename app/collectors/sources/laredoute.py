"""
Collector La Redoute - Extraction via Web Unlocker.
"""
import re
from typing import Optional, List

import requests
from bs4 import BeautifulSoup

from app.normalizers.item import DealItem
from app.core.exceptions import DataExtractionError, NetworkError, ValidationError
from app.services.proxy_service import get_web_unlocker_proxy

SOURCE = "laredoute"
BASE_URL = "https://www.laredoute.fr"


def fetch_laredoute_product(url: str) -> DealItem:
    """Récupère et parse un produit La Redoute."""
    proxy = get_web_unlocker_proxy()
    
    try:
        resp = requests.get(url, proxies=proxy, timeout=60, verify=False)
        if resp.status_code != 200:
            raise NetworkError(f"HTTP {resp.status_code}", source=SOURCE, url=url)
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Titre
        title_tag = soup.find('h1')
        title = title_tag.get_text(strip=True) if title_tag else None
        if not title:
            title_match = re.search(r'"name":\s*"([^"]+)"', resp.text)
            title = title_match.group(1) if title_match else None
        
        if not title:
            raise DataExtractionError("Titre non trouvé", source=SOURCE, url=url)
        
        # Prix
        price = None
        original_price = None
        
        # Chercher dans JSON-LD
        price_match = re.search(r'"price":\s*"?([0-9.]+)"?', resp.text)
        if price_match:
            price = float(price_match.group(1))
        
        if not price:
            raise ValidationError("Prix non trouvé", field="price", source=SOURCE, url=url)
        
        # Prix barré
        was_match = re.search(r'"wasPrice":\s*"?([0-9.]+)"?', resp.text)
        if was_match:
            original_price = float(was_match.group(1))
        
        # Discount
        discount_percent = None
        if original_price and original_price > price:
            discount_percent = round((1 - price / original_price) * 100, 1)
        
        # Image
        image_url = None
        img_match = re.search(r'"image":\s*"([^"]+)"', resp.text)
        if img_match:
            image_url = img_match.group(1)
        
        # External ID
        id_match = re.search(r'prod-([0-9]+)', url)
        external_id = id_match.group(1) if id_match else url.split('/')[-1]
        
        # Marque
        brand_match = re.search(r'"brand":\s*\{[^}]*"name":\s*"([^"]+)"', resp.text)
        brand = brand_match.group(1) if brand_match else "La Redoute"
        
        return DealItem(
            source=SOURCE,
            external_id=external_id,
            title=title,
            price=price,
            original_price=original_price,
            discount_percent=discount_percent,
            currency="EUR",
            url=url,
            image_url=image_url,
            brand=brand,
            seller_name="La Redoute",
        )
        
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"Erreur réseau: {e}", source=SOURCE, url=url)


def discover_laredoute_products(limit: int = 50) -> List[str]:
    """Découvre les URLs de produits sneakers sur La Redoute."""
    proxy = get_web_unlocker_proxy()
    urls = []
    
    listing_urls = [
        'https://www.laredoute.fr/lndng/ctlg.aspx?artcl=basket-blanche',
        'https://www.laredoute.fr/lndng/ctlg.aspx?artcl=sneakers-homme',
        'https://www.laredoute.fr/lndng/ctlg.aspx?artcl=basket-homme',
    ]
    
    for listing_url in listing_urls:
        try:
            resp = requests.get(listing_url, proxies=proxy, timeout=60, verify=False)
            if resp.status_code == 200:
                links = re.findall(r'href="(/ppdp/prod-[^"]+)"', resp.text)
                for link in set(links):
                    # Nettoyer l'URL
                    clean_url = link.split('#')[0]
                    full_url = BASE_URL + clean_url
                    if full_url not in urls:
                        urls.append(full_url)
                    if len(urls) >= limit:
                        break
        except Exception as e:
            print(f"Error discovering La Redoute products: {e}")
        
        if len(urls) >= limit:
            break
    
    return urls[:limit]
