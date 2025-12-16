"""
Vinted Service - REACTIVÉ (Mode Hybrid/Sniper)
Utilise le Browser Worker pour scraper Vinted de manière ciblée et économique.
"""

import urllib.parse
import statistics
import re
from typing import Optional, Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup

from app.services.browser_worker import browser_fetch_sync
from app.services.proxy_service import get_web_unlocker_proxy

class VintedService:
    """
    Service pour récupérer les données de marché Vinted.
    Utilise Playwright via BrowserWorker pour contourner les protections.
    """
    
    BASE_URL = "https://www.vinted.fr"
    
    def __init__(self):
        self.logger = logger.bind(service="vinted_scraper")

    def _build_search_url(self, query: str, brand: Optional[str] = None) -> str:
        """Construit l'URL de recherche Vinted."""
        # Nettoyage basique de la query
        clean_query = re.sub(r'\s+', ' ', query).strip()
        
        params = {
            "search_text": clean_query,
            "order": "relevance", # Pertinence pour avoir les items les plus proches
            "status_ids[]": [6, 1, 2], # Neuf avec/sans étiquette + Très bon état
        }
        
        # Si la marque est connue, on pourrait l'ajouter en filtre, 
        # mais les IDs de marque Vinted sont spécifiques non connus ici.
        # On fait confiance à la recherche textuelle pour l'instant.
        
        query_string = urllib.parse.urlencode(params, doseq=True)
        return f"{self.BASE_URL}/catalog?{query_string}"

    def _extract_price(self, price_text: str) -> Optional[float]:
        """Extrait un float d'une chaine de prix (ex: '12,50 €')."""
        try:
            # Garder chiffres et virgules/points
            clean = re.sub(r'[^\d.,]', '', price_text)
            clean = clean.replace(',', '.')
            return float(clean)
        except (ValueError, TypeError):
            return None

    def _parse_search_page(self, html_content: str) -> List[float]:
        """Parse le HTML pour extraire les prix des résultats visible."""
        prices = []
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Sélecteur générique pour les items produits Vinted
            # Vinted utilise souvent des data-testid
            # 2024: Les items sont souvent des 'div' avec data-testid="grid-item"
            items = soup.find_all('div', attrs={"data-testid": "grid-item"})
            
            if not items:
                # Fallback: recherche de classes si data-testid change
                # Cette partie est fragile et devra être maintenue
                self.logger.warning("No grid items found with data-testid, page stricture might have changed.")
                
            for item in items:
                # Essayer de trouver le prix dans l'item
                # Souvent dans un element avec un texte contenant "€"
                price_elem = item.find(string=re.compile(r'\d+[,.]\d+\s*€'))
                if not price_elem:
                     # Parfois le symbole est séparé
                     price_elem = item.find(string=re.compile(r'\d+[,.]\d+'))
                
                if price_elem:
                    p = self._extract_price(price_elem)
                    if p is not None:
                        prices.append(p)
                        
            # Filtrage des outliers (prix extrêmes qui faussent la moyenne)
            # On enlève les 10% les moins chers (fakes/erreurs) et 10% les plus chers
            if len(prices) > 5:
                prices.sort()
                cut = int(len(prices) * 0.1)
                prices = prices[cut:-cut]
                
        except Exception as e:
            self.logger.error(f"Error parsing Vinted HTML: {e}")
            
        return prices

    def get_market_stats(self, product_name: str, brand: Optional[str] = None, current_price: float = 0) -> Dict[str, Any]:
        """
        Récupère les stats de marché pour un produit sur Vinted.
        """
        url = self._build_search_url(product_name, brand)
        self.logger.info(f"Scraping Vinted: {product_name} -> {url}")
        
        # Tracer si on utilise un proxy Web Unlocker
        use_proxy = get_web_unlocker_proxy() is not None
        
        # Appel via Browser Worker (Synchrone pour l'instant pour compatibilité RQ)
        # On attend l'apparition de la grille pour être sûr
        content, error, meta = browser_fetch_sync(
            target="vinted", 
            url=url, 
            timeout=30,
            wait_for_selector='div[data-testid="grid-item"]'
        )
        
        if error.value != "success" or not content:
            self.logger.warning(f"Vinted scrape failed: {error.value} - {meta}")
            return self._empty_stats(error.value)
            
        prices = self._parse_search_page(content)
        
        if not prices:
            self.logger.info("No prices found on Vinted page")
            return self._empty_stats("no_results")
            
        # Calcul des stats statistiques
        stats = {
            "nb_listings": len(prices),
            "price_min": min(prices),
            "price_max": max(prices),
            "price_avg": round(sum(prices) / len(prices), 2),
            "price_median": round(statistics.median(prices), 2),
            "price_p25": round(statistics.quantiles(prices, n=4)[0], 2) if len(prices) >= 4 else min(prices),
            "price_p75": round(statistics.quantiles(prices, n=4)[2], 2) if len(prices) >= 4 else max(prices),
            "sample_listings": prices[:10], # Keep a sample
            "source_type": "vinted_live",
            "query_used": url
        }
        
        # Calcul marge par rapport au prix actuel (si fourni)
        target_resale = stats["price_median"]
        fees = (target_resale * 0.05) + 0.70 # Frais acheteur approx (ne s'applique pas au vendeur mais influence le prix d'achat)
        # Pour le vendeur, c'est gratuit sur Vinted, mais l'expédition est à charge acheteur.
        # On considère le prix brut comme net vendeur (simplification Vinted)
        
        margin_euro = target_resale - current_price
        margin_pct = (margin_euro / current_price * 100) if current_price > 0 else 0
        
        stats["margin_euro"] = round(margin_euro, 2)
        stats["margin_pct"] = round(margin_pct, 2)
        
        # Score de liquidité basé sur le nombre de résultats (simplifié)
        # > 20 résultats = très liquide
        stats["liquidity_score"] = min(len(prices) * 5, 100)
        
        self.logger.info(f"Vinted stats: Median={stats['price_median']}€, Listings={stats['nb_listings']}")
        return stats

    def _empty_stats(self, reason: str = "") -> Dict[str, Any]:
        """Retourne un objet stats vide."""
        return {
            "nb_listings": 0,
            "price_min": 0,
            "price_max": 0,
            "price_avg": 0,
            "price_median": 0,
            "price_p25": 0,
            "price_p75": 0,
            "margin_euro": 0,
            "margin_pct": 0,
            "liquidity_score": 0,
            "source_type": "error",
            "error_reason": reason,
            "sample_listings": []
        }

# Instance singleton
vinted_service = VintedService()

async def get_vinted_stats_for_deal(
    product_name: str, 
    brand: Optional[str] = None, 
    sale_price: float = 0
) -> Dict[str, Any]:
    """
    Wrapper async pour l'appel service.
    """
    # Exécuter de manière synchrone car browser_fetch_sync gère l'event loop pour Playwright
    # ou utiliser run_in_executor si on veut pas bloquer
    return vinted_service.get_market_stats(product_name, brand, sale_price)
