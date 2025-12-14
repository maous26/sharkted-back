"""
Scraping Service - Découverte et collecte automatique des deals.

Ce service utilise l'orchestrateur pour:
1. Choisir automatiquement la meilleure méthode de scraping
2. Gérer les fallbacks (HTTP → proxy → browser)
3. Logger les métriques pour optimisation

Workflow:
1. discover_products() - Crawle les pages de listing
2. Pour chaque produit trouvé, collecte les détails
3. Sauvegarde en DB et notifie les users
"""
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field

from sqlalchemy import text

from app.core.logging import get_logger
from app.core.source_policy import SOURCE_POLICIES, get_policy, CollectMode
from app.services.scraping_orchestrator import (
    get_orchestrator,
    get_target_config,
    ErrorType,
    ScrapingMethod,
    configure_proxies,
)
from app.core.proxy_config import load_proxy_config
from app.db.session import SessionLocal

logger = get_logger(__name__)


# =============================================================================
# INITIALIZATION - Charger la config des proxies au démarrage
# =============================================================================

def init_scraping_service():
    """Initialise le service de scraping avec la config des proxies."""
    proxy_config = load_proxy_config()
    configure_proxies(proxy_config)
    logger.info("Scraping service initialized")


# Appeler à l'import
try:
    init_scraping_service()
except Exception as e:
    logger.warning(f"Failed to initialize scraping service: {e}")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ScrapingResult:
    """Résultat d'un scraping."""
    source: str
    status: str  # success, partial, error, skipped
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0
    products_found: int = 0
    products_new: int = 0
    products_updated: int = 0
    errors: List[str] = field(default_factory=list)
    method_used: str = "http_direct"
    
    def to_dict(self) -> Dict:
        return {
            "id": str(uuid.uuid4()),
            "source_slug": self.source,
            "source_name": self.source.capitalize(),
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "deals_found": self.products_found,
            "deals_new": self.products_new,
            "deals_updated": self.products_updated,
            "errors": self.errors,
            "method_used": self.method_used,
        }


# =============================================================================
# URL CONFIGURATION
# =============================================================================

# URLs de listing par source - PRIORITE AUX SOLDES ET PROMOTIONS
SOURCE_LISTING_URLS: Dict[str, List[str]] = {
    "courir": [
        # Promotions en priorité
        "https://www.courir.com/fr/c/promotions-en-cours/",
        "https://www.courir.com/fr/c/promotions/",
        # Puis collections normales pour détecter les erreurs de prix
        "https://www.courir.com/fr/c/homme/chaussures/",
        "https://www.courir.com/fr/c/femme/chaussures/",
    ],
    "footlocker": [
        # Nécessite proxy résidentiel - désactivé pour l'instant
        "https://www.footlocker.fr/fr/category/soldes.html",
        "https://www.footlocker.fr/category/hommes/chaussures.html",
    ],
    "size": [
        # SOLDES en priorité absolue (UK = meilleures promos)
        "https://www.size.co.uk/sale/",
        "https://www.size.co.uk/mens/footwear/sale/",
        "https://www.size.co.uk/womens/footwear/sale/",
        # Nouveautés pour erreurs de prix
        "https://www.size.co.uk/mens/footwear/",
    ],
    "jdsports": [
        # Promotions en priorité
        "https://www.jdsports.fr/promo/",
        "https://www.jdsports.fr/homme/chaussures-homme/promo/",
        "https://www.jdsports.fr/femme/chaussures-femme/promo/",
        # Collections normales pour erreurs de prix
        "https://www.jdsports.fr/homme/chaussures-homme/baskets/",
        "https://www.jdsports.fr/femme/chaussures-femme/baskets/",
    ],
    "adidas": [
        # Nécessite proxy résidentiel - désactivé pour l'instant
        "https://www.adidas.fr/hommes-chaussures-outlet",
        "https://www.adidas.fr/femmes-chaussures-outlet",
    ],
    "zalando": [
        # Nécessite proxy résidentiel - désactivé pour l'instant
        "https://www.zalando.fr/promo-homme/",
        "https://www.zalando.fr/promo-femme/",
    ],
    "snipes": [
        # Soldes Snipes
        "https://www.snipes.fr/c/sale/",
        "https://www.snipes.fr/c/sale/?gender=Men",
        "https://www.snipes.fr/c/sale/?gender=Women",
    ],
}

# Patterns pour extraire les URLs de produits
PRODUCT_URL_PATTERNS: Dict[str, List[str]] = {
    "courir": [
        r'href="(https://www\.courir\.com/fr/p/[^"]+\.html)"',
        r'href="(/fr/p/[^"]+\.html)"',
    ],
    "footlocker": [
        r'href="(https://www\.footlocker\.fr/[^"]+/product/[^"]+)"',
        r'href="(/fr/product/[^"]+)"',
        r'href="(/product/[^"]+)"',
    ],
    "size": [
        r'href="(https://www\.size\.co\.uk/product/[^"]+)"',
        r'href="(/product/[^"]+)"',
    ],
    "jdsports": [
        r'href="(https://www\.jdsports\.fr/product/[^"]+)"',
        r'href="(/product/[^"]+)"',
    ],
    "adidas": [
        r'href="(https://www\.adidas\.fr/[^"]+\.html)"',
        r'data-auto-id="product-card-title" href="([^"]+)"',
    ],
    "zalando": [
        r'href="(https://www\.zalando\.fr/[^"]+\.html)"',
    ],
}

# Base URLs pour construire les URLs complètes
BASE_URLS: Dict[str, str] = {
    "courir": "https://www.courir.com",
    "footlocker": "https://www.footlocker.fr",
    "size": "https://www.size.co.uk",
    "jdsports": "https://www.jdsports.fr",
    "adidas": "https://www.adidas.fr",
    "zalando": "https://www.zalando.fr",
}


# =============================================================================
# CORE FUNCTIONS
# =============================================================================

def extract_product_urls(html: str, source: str) -> Set[str]:
    """Extrait les URLs de produits depuis le HTML d'une page de listing."""
    urls = set()
    patterns = PRODUCT_URL_PATTERNS.get(source, [])
    base_url = BASE_URLS.get(source, "")
    
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            url = match
            # Compléter les URLs relatives
            if url.startswith("/"):
                url = base_url + url
            # Nettoyer l'URL
            url = url.split("?")[0]  # Enlever les query params
            url = url.split("#")[0]  # Enlever les ancres
            if url.startswith("http"):
                urls.add(url)
    
    return urls


def crawl_listing_page(url: str, source: str) -> tuple[Set[str], Optional[str], str]:
    """
    Crawle une page de listing via l'orchestrateur.
    
    Returns:
        Tuple (urls_found, error_message, method_used)
    """
    orchestrator = get_orchestrator()
    
    content, error_type, metadata = orchestrator.fetch(source, url)
    method_used = metadata.get("method", "http_direct")
    
    if error_type == ErrorType.SUCCESS and content:
        urls = extract_product_urls(content, source)
        logger.info(
            f"Crawled listing page",
            source=source,
            url=url,
            products_found=len(urls),
            method=method_used,
            duration_ms=metadata.get("duration_ms"),
        )
        return urls, None, method_used
    else:
        error_msg = f"{error_type.value}"
        if metadata.get("status_code"):
            error_msg = f"HTTP {metadata['status_code']}"
        return set(), error_msg, method_used


def discover_products(source: str) -> tuple:
    """
    Découvre les produits pour une source.
    
    Returns:
        Tuple (ScrapingResult, Set[str] product_urls)
    """
    result = ScrapingResult(
        source=source,
        status="running",
        started_at=datetime.utcnow(),
    )
    
    # Vérifier si la source est activée dans la policy
    policy = get_policy(source)
    if not policy.enabled:
        result.status = "skipped"
        result.errors.append(f"Source disabled: {policy.reason}")
        result.completed_at = datetime.utcnow()
        return result, set()
    
    # Obtenir la config de la cible
    target_config = get_target_config(source)
    
    # Vérifier si on a les proxies nécessaires pour cette cible
    from app.services.scraping_orchestrator import _proxy_pool, ScrapingMethod
    
    if ScrapingMethod.HTTP_RESIDENTIAL in target_config.allowed_methods:
        if target_config.allowed_methods[0] == ScrapingMethod.HTTP_RESIDENTIAL:
            # Cette source REQUIERT des proxies résidentiels
            if not _proxy_pool.residential:
                result.status = "skipped"
                result.errors.append("Requires residential proxies - not configured yet")
                result.completed_at = datetime.utcnow()
                logger.info(
                    f"Skipping {source} - requires residential proxies",
                    source=source,
                )
                return result, set()
    
    # Obtenir les URLs de listing
    listing_urls = SOURCE_LISTING_URLS.get(source, [])
    if not listing_urls:
        result.status = "skipped"
        result.errors.append("No listing URLs configured")
        result.completed_at = datetime.utcnow()
        return result, set()
    
    all_product_urls: Set[str] = set()
    methods_used = set()
    
    # Crawler chaque page de listing
    for listing_url in listing_urls:
        urls, error, method = crawl_listing_page(listing_url, source)
        methods_used.add(method)
        
        if error:
            result.errors.append(f"{listing_url}: {error}")
        all_product_urls.update(urls)
        
        # Pause entre les pages (rate limiting)
        time.sleep(1 / target_config.requests_per_second)
    
    result.products_found = len(all_product_urls)
    result.completed_at = datetime.utcnow()
    result.duration_seconds = (result.completed_at - result.started_at).total_seconds()
    result.method_used = ", ".join(methods_used) if methods_used else "none"
    
    if result.products_found > 0:
        result.status = "success" if not result.errors else "partial"
    else:
        result.status = "error" if result.errors else "empty"
    
    logger.info(
        f"Product discovery completed",
        source=source,
        products_found=result.products_found,
        duration_sec=result.duration_seconds,
        status=result.status,
        methods=result.method_used,
    )
    
    return result, all_product_urls


def get_enabled_sources() -> List[str]:
    """Retourne la liste des sources activées."""
    return [
        source for source, policy in SOURCE_POLICIES.items()
        if policy.enabled and policy.mode != CollectMode.BLOCKED
    ]


def get_scrapable_sources() -> List[str]:
    """
    Retourne les sources qu'on peut effectivement scraper.
    
    Exclut les sources qui nécessitent des proxies non configurés.
    """
    from app.services.scraping_orchestrator import _proxy_pool, ScrapingMethod
    
    scrapable = []
    for source in get_enabled_sources():
        target_config = get_target_config(source)
        
        # Vérifier si la source nécessite des proxies résidentiels
        if target_config.allowed_methods:
            first_method = target_config.allowed_methods[0]
            if first_method == ScrapingMethod.HTTP_RESIDENTIAL:
                if not _proxy_pool.residential:
                    continue  # Skip cette source
        
        scrapable.append(source)
    
    return scrapable


# =============================================================================
# DATABASE STORAGE FOR LOGS
# =============================================================================

def add_scraping_log(result: ScrapingResult):
    """Ajoute un log de scraping en base de données."""
    session = SessionLocal()
    try:
        session.execute(
            text("""
                INSERT INTO scraping_logs 
                (source_slug, source_name, status, started_at, completed_at, 
                 duration_seconds, deals_found, deals_new, deals_updated, errors)
                VALUES (:source_slug, :source_name, :status, :started_at, :completed_at,
                        :duration_seconds, :deals_found, :deals_new, :deals_updated, :errors)
            """),
            {
                "source_slug": result.source,
                "source_name": result.source.capitalize(),
                "status": result.status,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "duration_seconds": result.duration_seconds,
                "deals_found": result.products_found,
                "deals_new": result.products_new,
                "deals_updated": result.products_updated,
                "errors": result.errors[:10] if result.errors else [],
            }
        )
        session.commit()
    except Exception as e:
        logger.error(f"Failed to save scraping log: {e}")
        session.rollback()
    finally:
        session.close()


def get_scraping_logs(page: int = 1, page_size: int = 20) -> Dict:
    """Retourne les logs de scraping paginés depuis la DB."""
    session = SessionLocal()
    try:
        # Count total
        count_result = session.execute(text("SELECT COUNT(*) FROM scraping_logs"))
        total = count_result.scalar() or 0
        
        # Get paginated logs
        offset = (page - 1) * page_size
        result = session.execute(
            text("""
                SELECT id, source_slug, source_name, status, started_at, completed_at,
                       duration_seconds, deals_found, deals_new, deals_updated, errors
                FROM scraping_logs 
                ORDER BY started_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": page_size, "offset": offset}
        )
        
        logs = []
        for row in result:
            logs.append({
                "id": str(row[0]),
                "source_slug": row[1],
                "source_name": row[2],
                "status": row[3],
                "started_at": row[4].isoformat() if row[4] else None,
                "completed_at": row[5].isoformat() if row[5] else None,
                "duration_seconds": row[6],
                "deals_found": row[7],
                "deals_new": row[8],
                "deals_updated": row[9],
                "errors": row[10] or [],
            })
        
        return {
            "logs": logs,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }
    except Exception as e:
        logger.error(f"Failed to get scraping logs: {e}")
        return {"logs": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}
    finally:
        session.close()


def delete_scraping_log(log_id: str) -> bool:
    """Supprime un log de scraping."""
    session = SessionLocal()
    try:
        result = session.execute(
            text("DELETE FROM scraping_logs WHERE id = :id"),
            {"id": log_id}
        )
        session.commit()
        return result.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to delete scraping log: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def delete_old_scraping_logs(older_than_days: int) -> int:
    """Supprime les logs plus vieux que X jours."""
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        result = session.execute(
            text("DELETE FROM scraping_logs WHERE started_at < :cutoff"),
            {"cutoff": cutoff}
        )
        session.commit()
        return result.rowcount
    except Exception as e:
        logger.error(f"Failed to delete old scraping logs: {e}")
        session.rollback()
        return 0
    finally:
        session.close()
