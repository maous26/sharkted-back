"""
StockX Service - Récupération des données marché via Web Unlocker
Utilise BrightData Web Unlocker pour contourner les protections StockX.
"""

import httpx
import re
import json
from typing import Optional, Dict, Any
from loguru import logger

from app.services.proxy_service import get_web_unlocker_proxy

class StockXService:
    """
    Service pour récupérer les données de revente sur StockX.
    Utilise le Web Unlocker pour contourner les protections.
    """
    
    SEARCH_URL = "https://stockx.com/api/browse"
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://stockx.com/",
        "Origin": "https://stockx.com",
    }
    
    def __init__(self):
        self.logger = logger.bind(service="stockx_service")

    def _clean_query(self, query: str) -> str:
        """Nettoie la query de recherche."""
        query = re.sub(r"[^a-zA-Z0-9\s-]", "", query)
        stopwords = ["wmns", "mens", "gs", "ps", "td", "preschool", "toddler", "grade school"]
        words = query.lower().split()
        words = [w for w in words if w not in stopwords]
        return " ".join(words[:5])

    def search_product(self, product_name: str, brand: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Recherche un produit sur StockX via Web Unlocker.
        """
        query = f"{brand} {product_name}".strip() if brand else product_name
        query = self._clean_query(query)
        
        self.logger.info(f"Searching StockX for: {query}")
        
        proxy_config = get_web_unlocker_proxy()
        if not proxy_config:
            self.logger.warning("No Web Unlocker proxy configured")
            return None
        
        # Convertir le format de proxy pour httpx
        # proxy_config = {"http": "http://...", "https": "http://..."}
        # httpx veut {"http://": "http://...", "https://": "http://..."}
        proxies = {
            "http://": proxy_config.get("http") or proxy_config.get("https"),
            "https://": proxy_config.get("https") or proxy_config.get("http"),
        }
        
        try:
            with httpx.Client(
                timeout=30,
                proxies=proxies,
                verify=False
            ) as client:
                response = client.get(
                    self.SEARCH_URL,
                    params={
                        "_search": query,
                        "page": 1,
                        "resultsPerPage": 5,
                        "dataType": "product",
                        "country": "FR",
                        "currency": "EUR"
                    },
                    headers=self.HEADERS
                )
                
                self.logger.info(f"StockX response status: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    products = data.get("Products", [])
                    
                    if products:
                        best = products[0]
                        self.logger.info(f"Found StockX product: {best.get(title, Unknown)}")
                        return best
                    else:
                        self.logger.warning(f"No StockX results for: {query}")
                        return None
                else:
                    self.logger.warning(f"StockX API returned {response.status_code}")
                    return None
                    
        except Exception as e:
            self.logger.error(f"StockX search error: {e}")
            return None

    def get_market_data(self, product_name: str, brand: Optional[str] = None, current_price: float = 0) -> Dict[str, Any]:
        """
        Récupère les données de marché StockX pour un produit.
        """
        product = self.search_product(product_name, brand)
        
        if not product:
            return self._empty_stats("no_match")
        
        try:
            market = product.get("market", {})
            
            lowest_ask = market.get("lowestAsk", 0) or 0
            highest_bid = market.get("highestBid", 0) or 0
            last_sale = market.get("lastSale", 0) or 0
            sales_last_72h = market.get("salesLast72Hours", 0) or 0
            retail_price = product.get("retailPrice", 0) or 0
            
            # Volatilité
            volatility = 0
            if lowest_ask > 0 and highest_bid > 0:
                volatility = round((lowest_ask - highest_bid) / lowest_ask * 100, 1)
            
            # Premium
            price_premium = 0
            if retail_price > 0 and last_sale > 0:
                price_premium = round((last_sale - retail_price) / retail_price * 100, 1)
            
            # Marge (frais StockX ~12%)
            margin_euro = 0
            margin_pct = 0
            sell_price = lowest_ask if lowest_ask > 0 else last_sale
            if current_price > 0 and sell_price > 0:
                sell_price_after_fees = sell_price * 0.88
                margin_euro = round(sell_price_after_fees - current_price, 2)
                margin_pct = round((margin_euro / current_price) * 100, 1)
            
            liquidity_score = min(100, (sales_last_72h or 0) * 5)
            
            return {
                "source": "stockx",
                "product_name": product.get("title", ""),
                "product_url": f"https://stockx.com/{product.get(urlKey, )}",
                "image_url": product.get("media", {}).get("thumbUrl", ""),
                "lowest_ask": lowest_ask,
                "highest_bid": highest_bid,
                "last_sale": last_sale,
                "sales_last_72h": sales_last_72h,
                "retail_price": retail_price,
                "volatility": volatility,
                "price_premium": price_premium,
                "margin_euro": margin_euro,
                "margin_pct": margin_pct,
                "liquidity_score": liquidity_score,
                "error": None
            }
            
        except Exception as e:
            self.logger.error(f"Error extracting StockX data: {e}")
            return self._empty_stats("extraction_error")

    def _empty_stats(self, reason: str = "") -> Dict[str, Any]:
        return {
            "source": "stockx",
            "product_name": None,
            "product_url": None,
            "image_url": None,
            "lowest_ask": 0,
            "highest_bid": 0,
            "last_sale": 0,
            "sales_last_72h": 0,
            "retail_price": 0,
            "volatility": 0,
            "price_premium": 0,
            "margin_euro": 0,
            "margin_pct": 0,
            "liquidity_score": 0,
            "error": reason
        }


stockx_service = StockXService()


def get_stockx_stats_for_deal(product_name: str, brand: Optional[str] = None, sale_price: float = 0) -> Dict[str, Any]:
    """Helper function pour obtenir les stats StockX dun deal."""
    return stockx_service.get_market_data(product_name, brand, sale_price)
