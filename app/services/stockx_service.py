"""
StockX Service - Récupération des données marché via Scraping
Utilise BrowserWorker + Web Unlocker pour contourner les protections.
"""

from typing import Optional, Dict, Any, List
from loguru import logger
import re
import json
from app.services.browser_worker import browser_fetch_sync
from app.services.proxy_service import get_web_unlocker_proxy

class StockXService:
    """
    Service pour récupérer les données de revente sur StockX.
    Utilise Playwright pour exécuter le JS et récupérer les données via
    intercept ou parsing HTML.
    """
    
    BASE_URL = "https://stockx.com"
    SEARCH_URL = "https://stockx.com/search?s={query}"
    
    def __init__(self):
        self.logger = logger.bind(service="stockx_scraper")

    def get_market_data(self, product_name: str, brand: Optional[str] = None) -> Dict[str, Any]:
        """
        Récupère les données de marché StockX.
        """
        query = f"{brand} {product_name}".strip() if brand else product_name
        # Nettoyage query
        query = re.sub(r'[^a-zA-Z0-9\s]', '', query)
        
        search_url = self.SEARCH_URL.format(query=query.replace(" ", "%20"))
        
        self.logger.info(f"Searching StockX for: {query}")
        
        proxy = get_web_unlocker_proxy()
        
        # On fetch la page de recherche. 
        # StockX injecte les données dans un script __NEXT_DATA__ souvent
        content, error, meta = browser_fetch_sync(
            target="stockx",
            url=search_url,
            timeout=40,
            wait_for_selector='div[data-testid="search-results"]', # A adapter si le DOM change
            proxy_config=proxy
        )
        
        if error.value != "success" or not content:
            self.logger.warning(f"StockX search failed: {error.value}")
            return self._empty_stats(error.value)
            
        try:
            # Extraction des données JSON hydratées par Next.js
            # C'est la méthode la plus fiable sur les sites Next.js
            match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', content)
            
            best_match = None
            
            if match:
                data = json.loads(match.group(1))
                # Naviguer dans le JSON pour trouver les résultats
                # Structure typique StockX: props.pageProps.req.appContext.states.query.value.queries...
                # Ou plus simplement, on cherche dans le HTML si la structure JSON est trop complexe/changeante.
                
                # Approche Hybride: Regex sur le JSON brut pour trouver les "products"
                # StockX returne une liste "edges" ou "hits"
                
                # Pour faire simple et robuste V1: on cherche le premier "product" dans le JSON
                # On scanne le JSON pour trouver une liste de produits
                # (Simplification pour ce POC)
                pass 
                
            # Fallback parsing HTML direct
            # Chercher le premier résultat pertinent
            # StockX affiche souvent le "Lowest Ask" ou "Last Sale"
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            
            # Selecteur approximatif pour le premier résultat
            # StockX change souvent ses classes (CSS Modules)
            # On cherche par texte "Lowest Ask" ou un prix
            
            # TODO: StockX est TRÈS difficile à parser via HTML simple car classes offusquées.
            # L'idéal est l'API interne accessible via le __NEXT_DATA__
            
            # Simulation pour le POC si on ne trouve pas (évite de bloquer la démo)
            # Dans une vrai prod, on utiliserait une API Scraper spécialisée StockX
            
            return self._empty_stats("parsing_not_implemented_yet")
            
        except Exception as e:
            self.logger.error(f"Error parsing StockX data: {e}")
            return self._empty_stats("parsing_error")

    def _empty_stats(self, reason: str = "") -> Dict[str, Any]:
        return {
            "source": "stockx",
            "last_sale": 0,
            "lowest_ask": 0,
            "highest_bid": 0,
            "volatility": 0,
            "error": reason
        }

stockx_service = StockXService()
