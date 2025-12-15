"""
Service Vinted - Matching et récupération des stats de marché
Version 2.1: Recherche hybride + Proxy rotation pour éviter rate-limiting
"""

import asyncio
import re
import statistics
import random
import time
import json
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from pathlib import Path
import httpx
from loguru import logger


# Coefficient pour estimer prix neuf à partir de prix occasion
CONDITION_COEFFICIENTS = {
    "new_with_tags": 1.0,
    "new_without_tags": 0.95,
    "very_good": 0.85,
    "good": 0.75,
    "satisfactory": 0.65,
    "mixed": 1.35,
}

# IDs des états Vinted
VINTED_STATUS_IDS = {
    "new_with_tags": 6,
    "new_without_tags": 1,
    "very_good": 2,
    "good": 3,
    "satisfactory": 4,
}

# Configuration proxy
PROXY_CONFIG_PATH = Path("/app/config/proxies.json")


def load_proxy_config() -> Dict:
    """Charge la configuration des proxies."""
    try:
        if PROXY_CONFIG_PATH.exists():
            with open(PROXY_CONFIG_PATH) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Impossible de charger config proxy: {e}")
    return {"datacenter": [], "residential": []}


def get_proxy_url() -> Optional[str]:
    """Retourne l'URL d'un proxy datacenter actif."""
    config = load_proxy_config()
    proxies = [p for p in config.get("datacenter", []) if p.get("enabled")]
    
    if not proxies:
        return None
    
    # Sélection pondérée
    total_weight = sum(p.get("weight", 1) for p in proxies)
    rand = random.uniform(0, total_weight)
    cumulative = 0
    
    for proxy in proxies:
        cumulative += proxy.get("weight", 1)
        if rand <= cumulative:
            protocol = proxy.get("protocol", "http")
            endpoint = proxy.get("endpoint")
            auth = proxy.get("auth", {})
            
            if auth.get("user") and auth.get("password"):
                return f"{protocol}://{auth['user']}:{auth['password']}@{endpoint}"
            return f"{protocol}://{endpoint}"
    
    return None


class VintedService:
    """Service pour interagir avec Vinted et calculer les stats de marché"""

    BASE_URL = "https://www.vinted.fr"
    SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"

    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(self):
        self._cookies: Dict[str, str] = {}
        self._session_expires: Optional[datetime] = None
        self._user_agent = random.choice(self.USER_AGENTS)
        self._last_request_time = 0
        self._min_request_interval = 2.0  # Augmenté pour éviter rate-limit
        self._use_proxy = True  # Toujours utiliser proxy pour Vinted
        self._consecutive_errors = 0

    def _get_headers(self, with_cookies: bool = True, for_api: bool = False) -> Dict[str, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        if for_api:
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-origin"
            headers["Referer"] = f"{self.BASE_URL}/catalog"
            headers["X-Requested-With"] = "XMLHttpRequest"
        if with_cookies and self._cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in self._cookies.items()])
            headers["Cookie"] = cookie_str
        return headers

    def _get_client_config(self) -> Dict:
        """Retourne la configuration du client HTTP avec proxy si disponible."""
        config = {
            "timeout": 30.0,
            "follow_redirects": True,
        }
        
        if self._use_proxy:
            proxy_url = get_proxy_url()
            if proxy_url:
                config["proxy"] = proxy_url
                logger.debug(f"Utilisation proxy: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")
        
        return config

    def _is_session_valid(self) -> bool:
        if not self._cookies or not self._session_expires:
            return False
        return datetime.now() < self._session_expires

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        # Ajouter jitter aléatoire
        delay = self._min_request_interval + random.uniform(0.5, 1.5)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.time()

    async def _init_session(self, force: bool = False) -> bool:
        if not force and self._is_session_valid():
            return True
        
        logger.info("Initialisation session Vinted...")
        self._user_agent = random.choice(self.USER_AGENTS)
        self._cookies = {}

        try:
            client_config = self._get_client_config()
            async with httpx.AsyncClient(**client_config) as client:
                await self._rate_limit()
                response = await client.get(self.BASE_URL, headers=self._get_headers(with_cookies=False))
                
                if response.status_code != 200:
                    logger.warning(f"Échec accès Vinted: {response.status_code}")
                    return False
                
                for cookie in response.cookies.jar:
                    if cookie.value:
                        self._cookies[cookie.name] = cookie.value
                
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
                    self._consecutive_errors = 0
                    logger.info(f"Session Vinted OK via proxy ({len(self._cookies)} cookies)")
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
        
        keep_words = {'air', 'force', 'max', 'dunk', 'jordan', '1', '90', '95', '97', 
                      'gel', '1130', '2002', 'samba', 'spezial', 'gazelle', 'campus',
                      'old', 'skool', 'sk8', 'authentic', 'era', '550', '530', '9060'}
        
        stop_words = {'le', 'la', 'les', 'de', 'du', 'des', 'un', 'une', 'pour',
                      'homme', 'femme', 'men', 'women', 'size', 'taille', 'new', 'neuf',
                      'chaussures', 'shoes', 'sneakers', 'basket', 'baskets', 'amp', '039'}
        
        for word in clean_name.lower().split():
            word = word.strip('-')
            if len(word) > 1 and word not in stop_words and word not in keywords:
                if word in keep_words or len(word) > 2:
                    keywords.append(word)
        
        return " ".join(keywords[:6])

    async def search_products(
        self, 
        query: str, 
        limit: int = 50, 
        status_ids: Optional[List[int]] = None,
        retry_count: int = 0
    ) -> List[Dict]:
        """Recherche des produits sur Vinted via proxy."""
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
        
        if status_ids:
            params["status_ids[]"] = status_ids
        
        try:
            await self._rate_limit()
            client_config = self._get_client_config()
            
            async with httpx.AsyncClient(**client_config) as client:
                response = await client.get(
                    self.SEARCH_URL,
                    params=params,
                    headers=self._get_headers(with_cookies=True, for_api=True)
                )
                
                if response.status_code == 200:
                    self._consecutive_errors = 0
                    data = response.json()
                    items = data.get("items", [])
                    status_str = f" (état={status_ids})" if status_ids else ""
                    logger.debug(f"Vinted: {len(items)} résultats pour '{query}'{status_str}")
                    return items
                    
                elif response.status_code == 401 and retry_count < 2:
                    logger.info("Session Vinted expirée, réinit...")
                    self._cookies = {}
                    self._session_expires = None
                    await asyncio.sleep(2)
                    return await self.search_products(query, limit, status_ids, retry_count + 1)
                    
                elif response.status_code in (403, 429):
                    self._consecutive_errors += 1
                    wait_time = min(30 * self._consecutive_errors, 120)
                    logger.warning(f"Vinted {response.status_code} - attente {wait_time}s (erreur #{self._consecutive_errors})")
                    
                    if retry_count < 2:
                        # Reset session et réessayer
                        self._cookies = {}
                        self._session_expires = None
                        self._user_agent = random.choice(self.USER_AGENTS)
                        await asyncio.sleep(wait_time)
                        return await self.search_products(query, limit, status_ids, retry_count + 1)
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

    def _extract_item_status(self, item: Dict) -> Optional[str]:
        status = item.get("status")
        if status:
            return status
        status_id = item.get("status_id")
        for name, sid in VINTED_STATUS_IDS.items():
            if sid == status_id:
                return name
        return None

    async def get_market_stats_hybrid(
        self, 
        product_name: str, 
        brand: Optional[str] = None,
        expected_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Récupère les stats de marché avec stratégie hybride:
        1. Cherche d'abord les articles "Neuf avec étiquette"
        2. Si < 5 résultats, cherche tous les articles et applique coefficient
        """
        query = self._build_search_query(product_name, brand)
        
        # Étape 1: Chercher articles neufs avec étiquette
        new_items = await self.search_products(
            query, 
            limit=50, 
            status_ids=[VINTED_STATUS_IDS["new_with_tags"]]
        )
        
        new_prices = []
        for item in new_items:
            price = self._extract_price(item)
            if price and price >= 5:
                new_prices.append(price)
        
        if len(new_prices) >= 5:
            logger.info(f"Vinted: {len(new_prices)} articles NEUFS pour '{query}'" )
            return self._build_stats(
                new_prices, 
                new_items, 
                query,
                source_type="new",
                coefficient=1.0
            )
        
        # Étape 2: Chercher tous les articles (mix)
        all_items = await self.search_products(query, limit=50)
        
        all_prices = []
        sample_listings = []
        
        for item in all_items:
            price = self._extract_price(item)
            if price and price >= 5:
                all_prices.append(price)
                if len(sample_listings) < 5:
                    photo = item.get("photo", {}) or {}
                    sample_listings.append({
                        "title": item.get("title", ""),
                        "price": price,
                        "url": f"{self.BASE_URL}/items/{item.get('id', '')}",
                        "photo_url": photo.get("url", "") if isinstance(photo, dict) else "",
                        "status": self._extract_item_status(item),
                    })
        
        if not all_prices:
            return {
                "nb_listings": 0, 
                "prices": [], 
                "query_used": query,
                "source_type": "none",
                "coefficient": 1.0,
            }
        
        coefficient = CONDITION_COEFFICIENTS["mixed"]
        logger.info(f"Vinted: {len(all_prices)} articles MIX pour '{query}' (coef x{coefficient})")
        
        return self._build_stats(
            all_prices,
            all_items,
            query,
            source_type="mixed",
            coefficient=coefficient,
            sample_listings=sample_listings
        )

    def _build_stats(
        self, 
        prices: List[float], 
        items: List[Dict],
        query: str,
        source_type: str,
        coefficient: float,
        sample_listings: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """Construit les statistiques à partir des prix."""
        
        if not prices:
            return {
                "nb_listings": len(items),
                "prices": [],
                "query_used": query,
                "source_type": source_type,
                "coefficient": coefficient,
            }
        
        initial_median = statistics.median(prices)
        filtered_prices = [p for p in prices if p <= initial_median * 3]
        if len(filtered_prices) < 3:
            filtered_prices = prices
        
        sorted_prices = sorted(filtered_prices)
        nb_prices = len(filtered_prices)
        
        raw_median = statistics.median(filtered_prices)
        raw_avg = statistics.mean(filtered_prices)
        raw_p25 = sorted_prices[max(0, int(nb_prices * 0.25) - 1)]
        raw_p75 = sorted_prices[min(nb_prices - 1, int(nb_prices * 0.75))]
        
        adjusted_median = round(raw_median * coefficient, 2)
        adjusted_avg = round(raw_avg * coefficient, 2)
        adjusted_p25 = round(raw_p25 * coefficient, 2)
        adjusted_p75 = round(raw_p75 * coefficient, 2)
        
        stats = {
            "nb_listings": len(items),
            "prices": filtered_prices,
            "query_used": query,
            "source_type": source_type,
            "coefficient": coefficient,
            "price_min_raw": round(min(filtered_prices), 2),
            "price_max_raw": round(max(filtered_prices), 2),
            "price_median_raw": round(raw_median, 2),
            "price_avg_raw": round(raw_avg, 2),
            "price_min": round(min(filtered_prices) * coefficient, 2),
            "price_max": round(max(filtered_prices) * coefficient, 2),
            "price_avg": adjusted_avg,
            "price_median": adjusted_median,
            "price_p25": adjusted_p25,
            "price_p75": adjusted_p75,
        }
        
        if sample_listings:
            stats["sample_listings"] = sample_listings
        elif items:
            stats["sample_listings"] = []
            for item in items[:5]:
                photo = item.get("photo", {}) or {}
                stats["sample_listings"].append({
                    "title": item.get("title", ""),
                    "price": self._extract_price(item),
                    "url": f"{self.BASE_URL}/items/{item.get('id', '')}",
                    "photo_url": photo.get("url", "") if isinstance(photo, dict) else "",
                })
        
        if nb_prices >= 2:
            stats["price_std"] = round(statistics.stdev(filtered_prices), 2)
            stats["coefficient_variation"] = round(stats["price_std"] / raw_avg * 100, 1) if raw_avg > 0 else 0
        
        return stats

    async def get_market_stats(self, product_name: str, brand: Optional[str] = None, expected_price: Optional[float] = None) -> Dict[str, Any]:
        """Alias vers la nouvelle méthode hybride."""
        return await self.get_market_stats_hybrid(product_name, brand, expected_price)

    def calculate_margin(self, buy_price: float, vinted_stats: Dict, use_percentile: str = "p25") -> Tuple[float, float]:
        """Calcule la marge en utilisant les prix ajustés."""
        if use_percentile == "p25":
            sell_price = vinted_stats.get("price_p25", 0)
        elif use_percentile == "p75":
            sell_price = vinted_stats.get("price_p75", 0)
        else:
            sell_price = vinted_stats.get("price_median", 0)
        
        if not sell_price or sell_price <= 0:
            return 0.0, 0.0
        
        vinted_fees = sell_price * 0.08
        shipping = 4.50
        net_sell_price = sell_price - vinted_fees - shipping
        
        margin_euro = net_sell_price - buy_price
        margin_percent = (margin_euro / buy_price * 100) if buy_price > 0 else 0
        
        return round(margin_euro, 2), round(margin_percent, 1)

    def calculate_liquidity_score(self, vinted_stats: Dict) -> float:
        """Calcule le score de liquidité."""
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


vinted_service = VintedService()


async def get_vinted_stats_for_deal(product_name: str, brand: Optional[str], sale_price: float) -> Dict[str, Any]:
    """Helper pour obtenir les stats Vinted complètes avec méthode hybride."""
    
    stats = await vinted_service.get_market_stats_hybrid(
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
            "sample_listings": [],
            "source_type": "none",
            "coefficient": 1.0,
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
        "price_median_raw": stats.get("price_median_raw"),
        "coefficient_variation": stats.get("coefficient_variation"),
        "margin_euro": margin_euro,
        "margin_pct": margin_pct,
        "liquidity_score": liquidity_score,
        "sample_listings": stats.get("sample_listings", []),
        "query_used": stats.get("query_used", ""),
        "source_type": stats.get("source_type", "mixed"),
        "coefficient": stats.get("coefficient", 1.0),
    }
