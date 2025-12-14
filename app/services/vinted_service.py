"""
Service Vinted - Matching et récupération des stats de marché
"""

import asyncio
import re
import statistics
import random
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
import httpx
from loguru import logger


class VintedService:
    """Service pour interagir avec Vinted et calculer les stats de marché"""

    BASE_URL = "https://www.vinted.fr"
    SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"

    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]

    def __init__(self):
        self._cookies: Dict[str, str] = {}
        self._session_expires: Optional[datetime] = None
        self._user_agent = random.choice(self.USER_AGENTS)
        self._last_request_time = 0
        self._min_request_interval = 1.5

    def _get_headers(self, with_cookies: bool = True, for_api: bool = False) -> Dict[str, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Connection": "keep-alive",
        }
        if for_api:
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Referer"] = f"{self.BASE_URL}/catalog"
        if with_cookies and self._cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in self._cookies.items()])
            headers["Cookie"] = cookie_str
        return headers

    def _is_session_valid(self) -> bool:
        if not self._cookies or not self._session_expires:
            return False
        return datetime.now() < self._session_expires

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed + random.uniform(0.1, 0.5))
        self._last_request_time = time.time()

    async def _init_session(self, force: bool = False) -> bool:
        if not force and self._is_session_valid():
            return True
        
        logger.info("Initialisation session Vinted...")
        self._user_agent = random.choice(self.USER_AGENTS)
        self._cookies = {}

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                await self._rate_limit()
                response = await client.get(self.BASE_URL, headers=self._get_headers(with_cookies=False))
                
                if response.status_code != 200:
                    logger.warning(f"Échec accès Vinted: {response.status_code}")
                    return False
                
                for cookie in response.cookies.jar:
                    if cookie.value:
                        self._cookies[cookie.name] = cookie.value
                
                # Tester l'API
                await self._rate_limit()
                test_response = await client.get(
                    self.SEARCH_URL,
                    params={"search_text": "nike", "per_page": 1},
                    headers=self._get_headers(with_cookies=True, for_api=True)
                )
                
                for cookie in test_response.cookies.jar:
                    if cookie.value:
                        self._cookies[cookie.name] = cookie.value
                
                if test_response.status_code == 200:
                    self._session_expires = datetime.now() + timedelta(minutes=30)
                    logger.info(f"Session Vinted OK ({len(self._cookies)} cookies)")
                    return True
                    
                return False
        except Exception as e:
            logger.error(f"Erreur init session Vinted: {e}")
            return False

    def _build_search_query(self, product_name: str, brand: Optional[str] = None) -> str:
        clean_name = re.sub(r'[^\w\s-]', ' ', product_name)
        keywords = []
        
        if brand and brand.lower() not in clean_name.lower():
            keywords.append(brand.lower())
        
        stop_words = {'le', 'la', 'les', 'de', 'du', 'des', 'un', 'une', 'pour',
                      'homme', 'femme', 'men', 'women', 'size', 'taille', 'new', 'neuf',
                      'chaussures', 'shoes', 'sneakers', 'basket', 'baskets'}
        
        for word in clean_name.lower().split():
            word = word.strip('-')
            if len(word) > 2 and word not in stop_words and word not in keywords:
                keywords.append(word)
        
        return " ".join(keywords[:5])

    async def search_products(self, query: str, limit: int = 50, retry_count: int = 0) -> List[Dict]:
        if not self._is_session_valid():
            if not await self._init_session():
                return []
        
        params = {
            "search_text": query,
            "per_page": min(limit, 96),
            "order": "relevance",
            "currency": "EUR",
            "page": 1,
            "price_from": 5,
        }
        
        try:
            await self._rate_limit()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.SEARCH_URL,
                    params=params,
                    headers=self._get_headers(with_cookies=True, for_api=True)
                )
                
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])
                    logger.debug(f"Vinted: {len(items)} résultats pour '{query}'")
                    return items
                    
                elif response.status_code == 401 and retry_count < 2:
                    logger.info("Session Vinted expirée, réinit...")
                    self._cookies = {}
                    self._session_expires = None
                    await asyncio.sleep(1)
                    return await self.search_products(query, limit, retry_count + 1)
                    
                elif response.status_code == 429:
                    logger.warning("Rate limit Vinted")
                    await asyncio.sleep(30)
                    if retry_count < 2:
                        return await self.search_products(query, limit, retry_count + 1)
                    return []
                else:
                    logger.warning(f"Vinted error: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Erreur recherche Vinted: {e}")
            return []

    def _extract_price(self, item: Dict) -> Optional[float]:
        try:
            price = item.get("price")
            if price:
                if isinstance(price, (int, float)):
                    return float(price)
                if isinstance(price, str):
                    match = re.search(r'(\d+[.,]?\d*)', price)
                    if match:
                        return float(match.group(1).replace(',', '.'))
            
            total_price = item.get("total_item_price")
            if total_price:
                if isinstance(total_price, dict):
                    amount = total_price.get("amount")
                    if amount:
                        return float(amount)
                elif isinstance(total_price, (int, float)):
                    return float(total_price)
            
            return None
        except:
            return None

    async def get_market_stats(self, product_name: str, brand: Optional[str] = None, expected_price: Optional[float] = None) -> Dict[str, Any]:
        query = self._build_search_query(product_name, brand)
        items = await self.search_products(query, limit=50)
        
        if not items:
            return {"nb_listings": 0, "prices": [], "query_used": query}
        
        prices = []
        sample_listings = []
        
        for item in items:
            price = self._extract_price(item)
            if price and price >= 5:
                prices.append(price)
                if len(sample_listings) < 5:
                    photo = item.get("photo", {}) or {}
                    sample_listings.append({
                        "title": item.get("title", ""),
                        "price": price,
                        "url": f"{self.BASE_URL}/items/{item.get('id', '')}",
                        "photo_url": photo.get("url", "") if isinstance(photo, dict) else "",
                    })
        
        if not prices:
            return {"nb_listings": len(items), "prices": [], "query_used": query}
        
        # Filtrer outliers
        initial_median = statistics.median(prices)
        filtered_prices = [p for p in prices if p <= initial_median * 3]
        if len(filtered_prices) < 3:
            filtered_prices = prices
        
        sorted_prices = sorted(filtered_prices)
        nb_prices = len(filtered_prices)
        
        stats = {
            "nb_listings": len(items),
            "prices": filtered_prices,
            "query_used": query,
            "price_min": round(min(filtered_prices), 2),
            "price_max": round(max(filtered_prices), 2),
            "price_avg": round(statistics.mean(filtered_prices), 2),
            "price_median": round(statistics.median(filtered_prices), 2),
            "price_p25": round(sorted_prices[max(0, int(nb_prices * 0.25) - 1)], 2),
            "price_p75": round(sorted_prices[min(nb_prices - 1, int(nb_prices * 0.75))], 2),
            "sample_listings": sample_listings,
        }
        
        if nb_prices >= 2:
            stats["price_std"] = round(statistics.stdev(filtered_prices), 2)
            stats["coefficient_variation"] = round(stats["price_std"] / stats["price_avg"] * 100, 1) if stats["price_avg"] > 0 else 0
        
        logger.info(f"Vinted stats pour '{query}': {nb_prices} prix, médiane {stats['price_median']}€")
        return stats

    def calculate_margin(self, buy_price: float, vinted_stats: Dict, use_percentile: str = "p25") -> Tuple[float, float]:
        if use_percentile == "p25":
            sell_price = vinted_stats.get("price_p25", 0)
        elif use_percentile == "p75":
            sell_price = vinted_stats.get("price_p75", 0)
        else:
            sell_price = vinted_stats.get("price_median", 0)
        
        if not sell_price or sell_price <= 0:
            return 0.0, 0.0
        
        # Frais Vinted: ~8% total
        vinted_fees = sell_price * 0.08
        shipping = 4.50
        net_sell_price = sell_price - vinted_fees - shipping
        
        margin_euro = net_sell_price - buy_price
        margin_percent = (margin_euro / buy_price * 100) if buy_price > 0 else 0
        
        return round(margin_euro, 2), round(margin_percent, 1)

    def calculate_liquidity_score(self, vinted_stats: Dict) -> float:
        nb_listings = vinted_stats.get("nb_listings", 0)
        
        if nb_listings == 0:
            return 0.0
        
        if nb_listings >= 50:
            listings_score = 60
        elif nb_listings >= 30:
            listings_score = 50
        elif nb_listings >= 15:
            listings_score = 40
        elif nb_listings >= 5:
            listings_score = 25
        else:
            listings_score = nb_listings * 4
        
        cv = vinted_stats.get("coefficient_variation", 100)
        if cv <= 15:
            dispersion_score = 40
        elif cv <= 25:
            dispersion_score = 30
        elif cv <= 40:
            dispersion_score = 20
        elif cv <= 60:
            dispersion_score = 10
        else:
            dispersion_score = 0
        
        return round(min(listings_score + dispersion_score, 100), 1)


# Instance singleton
vinted_service = VintedService()


async def get_vinted_stats_for_deal(product_name: str, brand: Optional[str], sale_price: float) -> Dict[str, Any]:
    """Helper pour obtenir les stats Vinted complètes."""
    
    stats = await vinted_service.get_market_stats(
        product_name=product_name,
        brand=brand,
        expected_price=sale_price * 2
    )
    
    if stats.get("nb_listings", 0) == 0 or not stats.get("prices"):
        return {
            "nb_listings": 0,
            "margin_euro": 0,
            "margin_pct": 0,
            "liquidity_score": 0,
            "sample_listings": []
        }
    
    margin_euro, margin_pct = vinted_service.calculate_margin(sale_price, stats, "p25")
    liquidity_score = vinted_service.calculate_liquidity_score(stats)
    
    return {
        "nb_listings": stats.get("nb_listings", 0),
        "price_min": stats.get("price_min"),
        "price_max": stats.get("price_max"),
        "price_avg": stats.get("price_avg"),
        "price_median": stats.get("price_median"),
        "price_p25": stats.get("price_p25"),
        "price_p75": stats.get("price_p75"),
        "coefficient_variation": stats.get("coefficient_variation"),
        "margin_euro": margin_euro,
        "margin_pct": margin_pct,
        "liquidity_score": liquidity_score,
        "sample_listings": stats.get("sample_listings", []),
        "query_used": stats.get("query_used", "")
    }
