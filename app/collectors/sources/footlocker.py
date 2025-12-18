"""
Collector Footlocker FR - Extraction de produits via JSON-LD avec support JavaScript.

Footlocker.fr est une SPA React qui nécessite un rendu JavaScript pour les pages de produits.
"""
import json
import re
import time
from typing import Optional

import cloudscraper
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from app.utils.http_stealth import create_stealth_scraper, get_stealth_headers, random_delay, get_proxy, should_use_proxy
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

SOURCE = "footlocker"

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _extract_sku_from_url(url: str) -> Optional[str]:
    """Extrait le SKU de l'URL (ex: .../314217910604.html -> 314217910604)."""
    match = re.search(r'/(\d{10,})\.html', url)
    return match.group(1) if match else None


def _is_category_page(url: str, html: str) -> bool:
    """Détermine si l'URL/page est une page de catégorie plutôt qu'un produit."""
    # Vérifier l'URL - patterns plus précis
    category_patterns = [
        r'/category/',
        r'/soldes\.html$',
        r'/nouveautes\.html$', 
        r'/outlet\.html$',
        r'/inspiration/',
        r'/flx\.html$',
    ]
    
    for pattern in category_patterns:
        if re.search(pattern, url):
            return True
    
    # Vérifier les meta robots noindex (indicateur de page catégorie)
    if re.search(r'<meta[^>]+name=["\']robots["\'][^>]+content=["\'][^"\']*(noindex)[^"\']', html, re.IGNORECASE):
        return True
    
    # Vérifier le titre de la page
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_match and title_match.group(1).strip() == "Foot Locker France":
        return True
    
    # Vérifier la présence d'éléments de navigation sans données produit
    nav_indicators = [
        r'HeaderNavigation',
        r'NavigationMenu',
        r'aria-label=["\']Principaux["\']',
        r'class=["\'][^"\']*(category|navigation|menu)[^"\']',
    ]
    
    nav_count = sum(1 for indicator in nav_indicators if re.search(indicator, html, re.IGNORECASE))
    
    # Chercher des JSON-LD Product
    has_product_jsonld = False
    for match in _JSONLD_RE.finditer(html):
        try:
            jsonld = json.loads(match.group(1).strip())
            if isinstance(jsonld, dict) and jsonld.get("@type") == "Product":
                has_product_jsonld = True
                break
        except (json.JSONDecodeError, KeyError):
            continue
    
    return nav_count >= 2 and not has_product_jsonld


def _create_selenium_driver() -> webdriver.Chrome:
    """Crée un driver Chrome headless avec options stealth."""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-web-security')
    options.add_argument('--disable-features=VizDisplayCompositor')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    # Désactiver les images et CSS pour plus de rapidité
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.managed_default_content_settings.stylesheets": 2
    }
    options.add_experimental_option("prefs", prefs)
    
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        raise NetworkError(f"Impossible de créer le driver Selenium: {e}", source=SOURCE)


def _extract_product_data_selenium(url: str) -> dict:
    """Extrait les données produit avec Selenium pour gérer JavaScript."""
    driver = None
    try:
        driver = _create_selenium_driver()
        driver.get(url)
        
        # Attendre que la page soit chargée
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "script"))
            )
        except TimeoutException:
            pass
        
        # Attendre un peu plus pour le rendu JavaScript
        time.sleep(3)
        
        html = driver.page_source
        return _extract_product_data(html, url)
        
    except WebDriverException as e:
        raise NetworkError(f"Erreur Selenium: {e}", source=SOURCE, url=url)
    finally:
        if driver:
            driver.quit()


def _extract_product_data(html: str, url: str) -> dict:
    """
    Extrait les données produit depuis le JSON-LD.
    Footlocker fournit un JSON-LD Product complet.
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

    # Chercher le JSON-LD Product
    for match in _JSONLD_RE.finditer(html):
        try:
            jsonld = json.loads(match.group(1).strip())
            if isinstance(jsonld, dict) and jsonld.get("@type") == "Product":
                data["name"] = jsonld.get("name")
                data["brand"] = jsonld.get("brand")
                data["sku"] = jsonld.get("sku") or data["sku"]

                # Image peut être string ou array
                image = jsonld.get("image")
                if isinstance(image, list):
                    data["image"] = image[0] if image else None
                else:
                    data["image"] = image

                # Prix dans offers
                offers = jsonld.get("offers", {})
                if isinstance(offers, dict):
                    data["price"] = offers.get("price")
                    data["currency"] = offers.get("priceCurrency", "EUR")
                elif isinstance(offers, list) and offers:
                    data["price"] = offers[0].get("price")
                    data["currency"] = offers[0].get("priceCurrency", "EUR")

                break
        except (json.JSONDecodeError, KeyError):
            continue
    
    # Conversion du prix en float si c'est une string
    if isinstance(data["price"], str):
        try:
            data["price"] = float(data["price"])
        except (ValueError, TypeError):
            data["price"] = None
    
    # Prix original (prix barré dans le HTML) - patterns plus robustes
    price_patterns = [
        r'class="[^"]*(?:was|strike|crossed|old|original|before)[^"]*"[^>]*>([^<]*[0-9]+[,.]?[0-9]*)',
        r'<del[^>]*>([^<]*[0-9]+[,.]?[0-9]*)</del>',
        r'data-[^=]*price[^=]*="([^"]*[0-9]+[,.]?[0-9]*)"',
    ]
    
    for pattern in price_patterns:
        was_price = re.search(pattern, html, re.IGNORECASE)
        if was_price:
            try:
                price_str = re.sub(r'[^\d.,]', '', was_price.group(1)).replace(",", ".")
                if price_str:
                    data["original_price"] = float(price_str)
                    break
            except ValueError:
                continue
    
    # Calcul discount_percent
    if data["price"] and data["original_price"] and data["original_price"] > data["price"]:
        data["discount_percent"] = round(
            (1 - data["price"] / data["original_price"]) * 100, 1
        )

    # Fallback: meta tags si JSON-LD incomplet
    if not data["name"]:
        og_title = re.search(r'<meta property="og:title"[^>]*content="([^"]+)"', html)
        if og_title:
            data["name"] = og_title.group(1).strip()

    if not data["image"]:
        og_image = re.search(r'<meta property="og:image"[^>]*content="([^"]+)"', html)
        if og_image:
            data["image"] = og_image.group(1)

    return data


@retry_on_network_errors(retries=2, source=SOURCE)
def fetch_footlocker_product(url: str) -> DealItem:
    """
    Récupère et parse un produit Footlocker FR.

    Raises:
        BlockedError: Si bloqué
        TimeoutError: Si timeout réseau
        NetworkError: Si erreur réseau
        HTTPError: Si erreur HTTP autre
        DataExtractionError: Si données non trouvées ou page de catégorie
        ValidationError: Si données invalides
    """
    # Première tentative avec cloudscraper
    scraper, headers = create_stealth_scraper("footlocker")
    use_selenium = False

    try:
        proxies = get_proxy() if should_use_proxy("footlocker") else None
        resp = scraper.get(url, headers=headers, proxies=proxies, timeout=30)
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

    # Vérifier le status HTTP
    if resp.status_code == 403:
        raise BlockedError(
            "Bloqué par protection anti-bot",
            source=SOURCE,
            url=url,
            status_code=403,
        )

    if resp.status_code == 404:
        raise DataExtractionError(
            "Produit non trouvé (404)",
            source=SOURCE,
            url=url,
        )

    # Pour les erreurs 400, vérifier si c'est une page de catégorie
    if resp.status_code == 400:
        # Vérifier directement l'URL pour les patterns de catégorie
        if _is_category_page(url, ""):
            raise DataExtractionError(
                "URL fournie est une page de catégorie, pas un produit individuel",
                source=SOURCE,
                url=url,
            )
        else:
            raise HTTPError(
                "Requête invalide (400) - Vérifiez l'URL",
                status_code=resp.status_code,
                source=SOURCE,
                url=url,
            )

    if resp.status_code >= 400:
        raise HTTPError(
            "Erreur HTTP",
            status_code=resp.status_code,
            source=SOURCE,
            url=url,
        )

    # Vérifier si c'est une page de catégorie
    if _is_category_page(url, resp.text):
        raise DataExtractionError(
            "URL fournie est une page de catégorie, pas un produit individuel",
            source=SOURCE,
            url=url,
        )

    # Vérifier si c'est une SPA avec contenu minimal (React)
    if '<div id="app">' in resp.text and len(resp.text) < 50000:
        use_selenium = True

    # Extraire les données
    if use_selenium:
        data = _extract_product_data_selenium(url)
    else:
        data = _extract_product_data(resp.text, url)

    # Si pas de données avec cloudscraper, essayer Selenium
    if not data["name"] and not use_selenium:
        data = _extract_product_data_selenium(url)

    # Validation
    if not data["name"]:
        raise DataExtractionError(
            "Nom du produit non trouvé",
            source=SOURCE,
            url=url,
        )

    if not data["price"] or data["price"] <= 0:
        raise ValidationError(
            f"Prix invalide: {data['price']}",
            field="price",
            source=SOURCE,
            url=url,
        )

    # Construire l'external_id
    external_id = data["sku"] or url.split("/")[-1].replace(".html", "")

    return DealItem(
        source=SOURCE,
        external_id=external_id,
        title=data["name"],
        price=data["price"],
        original_price=data.get("original_price"),
        discount_percent=data.get("discount_percent"),
        currency=data["currency"],
        url=url,
        image_url=data["image"],
        seller_name=data["brand"],
        brand=data["brand"],
        raw=data,
    )