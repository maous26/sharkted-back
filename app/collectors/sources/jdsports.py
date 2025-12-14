"""
Collector JD Sports FR - Extraction de produits avec prix soldés.

JD Sports FR (jdsports.fr) - données produit depuis:
- Meta tags Twitter (twitter:data1 = price, twitter:image:src = image)
- Meta tags standards (name="title" = nom)
- Variables JavaScript (brand, plu, unitPrice, wasPrice)
"""
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

SOURCE = "jdsports"


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL."""
    match = re.search(r'/(\d+_jdsportsfr)/?', url)
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

    # 1. Meta name="title" pour le nom complet
    title_meta = re.search(r'<meta name="title"\s+content="([^"]+)"', html, re.IGNORECASE)
    if title_meta:
        title = title_meta.group(1).strip()
        title = re.sub(r'\s*-?\s*JD Sports.*$', '', title, flags=re.IGNORECASE)
        data["name"] = title.strip()

    # 2. Twitter meta pour le prix actuel
    price_meta = re.search(r'<meta name="twitter:data1"\s+content="([^"]+)"', html, re.IGNORECASE)
    if price_meta:
        try:
            price_str = price_meta.group(1).replace(",", ".").replace("€", "").strip()
            data["price"] = float(price_str)
        except ValueError:
            pass

    # 3. Twitter meta pour l'image
    image_meta = re.search(r'<meta name="twitter:image:src"\s+content="([^"]+)"', html, re.IGNORECASE)
    if image_meta:
        data["image"] = image_meta.group(1)

    # 4. JavaScript pour la marque
    brand_js = re.search(r'brand:\s*"([^"]+)"', html)
    if brand_js:
        data["brand"] = brand_js.group(1)

    # 5. JavaScript pour le PLU (SKU)
    plu_js = re.search(r'plu:\s*"([^"]+)"', html)
    if plu_js:
        data["sku"] = plu_js.group(1)

    # 6. JavaScript pour les prix - recherche unitPrice et wasPrice
    unit_price_js = re.search(r'unitPrice:\s*"([0-9.]+)"', html)
    if unit_price_js:
        try:
            data["price"] = float(unit_price_js.group(1))
        except ValueError:
            pass
    
    # Prix original (wasPrice ou RRP)
    was_price_js = re.search(r'wasPrice:\s*"([0-9.]+)"', html)
    if was_price_js:
        try:
            data["original_price"] = float(was_price_js.group(1))
        except ValueError:
            pass
    
    # Alternative: RRP (prix de vente conseillé)
    if not data["original_price"]:
        rrp_js = re.search(r'rrp:\s*"([0-9.]+)"', html)
        if rrp_js:
            try:
                data["original_price"] = float(rrp_js.group(1))
            except ValueError:
                pass

    # 7. Prix barré dans le HTML (span class contenant "was" ou "strike")
    if not data["original_price"]:
        was_html = re.search(r'class="[^"]*(?:was|strike|original|crossed)[^"]*"[^>]*>([^<]*[0-9]+[,.]?[0-9]*)', html, re.IGNORECASE)
        if was_html:
            try:
                price_str = re.sub(r'[^\d.,]', '', was_html.group(1)).replace(",", ".")
                if price_str:
                    data["original_price"] = float(price_str)
            except ValueError:
                pass

    # 8. Calcul du pourcentage de remise
    if data["price"] and data["original_price"] and data["original_price"] > data["price"]:
        data["discount_percent"] = round(
            (1 - data["price"] / data["original_price"]) * 100, 1
        )

    # 9. Fallback nom depuis og:title
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_title:
            title = og_title.group(1).strip()
            title = re.sub(r'\s*-?\s*JD Sports.*$', '', title, flags=re.IGNORECASE)
            data["name"] = title.strip()

    # 10. Fallback image depuis og:image
    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"\s+content="([^"]+)"', html, re.IGNORECASE)
        if og_image:
            data["image"] = og_image.group(1)

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_jdsports_product(url: str) -> DealItem:
    """Récupère et parse un produit JD Sports FR."""
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

    external_id = data["sku"] or url.split("/")[-2]

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
