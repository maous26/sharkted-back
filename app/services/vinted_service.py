"""
Service Vinted - Matching et récupération des stats de marché
Version 2.2: Multi-strategy (direct + proxy rotation) pour contourner les blocages
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


CONDITION_COEFFICIENTS = {
    "new_with_tags": 1.0,
    "new_without_tags": 0.95,
    "very_good": 0.85,
    "good": 0.75,
    "satisfactory": 0.65,
    "mixed": 1.35,
}

VINTED_STATUS_IDS = {
    "new_with_tags": 6,
    "new_without_tags": 1,
    "very_good": 2,
    "good": 3,
    "satisfactory": 4,
}

PROXY_CONFIG_PATH = Path("/app/config/proxies.json")


def load_proxy_config() -> Dict:
    try:
        if PROXY_CONFIG_PATH.exists():
            with open(PROXY_CONFIG_PATH) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Impossible de charger config proxy: {e}")
    return {"datacenter": [], "residential": []}


def get_all_proxies() -> List[Dict]:
    """Retourne tous les proxies disponibles."""
    config = load_proxy_config()
    proxies = []
    
    for p in config.get("datacenter", []):
        if p.get("enabled"):
            protocol = p.get("protocol", "http")
            endpoint = p.get("endpoint")
            auth = p.get("auth", {})
            if auth.get("user") and auth.get("password"):
                url = f"{protocol}://{auth['user']}:{auth['password']}@{endpoint}"
            else:
                url = f"{protocol}://{endpoint}"
            proxies.append({"url": url, "name": p.get("name", endpoint), "type": "datacenter"})
    
    return proxies


class VintedService:
    BASE_URL = "https://www.vinted.fr"
    SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"

    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
    ]

    def __init__(self):
        self._sessions: Dict[str, Dict] = {}  # Stocke cookies par stratégie
        self._last_request_time = 0
        self._min_request_interval = 1.5
        self._failed_strategies: Dict[str, datetime] = {}  # Track stratégies en échec
        self._strategy_cooldown = 300  # 5 min cooldown après échec

    def _get_headers(self, cookies: Dict = None, for_api: bool = False) -> Dict[str, str]:
        ua = random.choice(self.USER_AGENTS)
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }
        if for_api:
            headers["Sec-Fetch-Dest"] = "empty"
            headers["Sec-Fetch-Mode"] = "cors"
            headers["Sec-Fetch-Site"] = "same-origin"
            headers["Referer"] = f"{self.BASE_URL}/catalog"
        if cookies:
            headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        return headers

    def _get_available_strategies(self) -> List[str]:
        """Retourne les stratégies disponibles (pas en cooldown)."""
        strategies = ["direct"]  # Toujours essayer direct en premier
        
        now = datetime.now()
        for proxy in get_all_proxies():
            name = proxy["name"]
            if name in self._failed_strategies:
                if (now - self._failed_strategies[name]).total_seconds() < self._strategy_cooldown:
                    continue  # Encore en cooldown
                else:
                    del self._failed_strategies[name]  # Cooldown terminé
            strategies.append(name)
        
        # Aussi vérifier direct
        if "direct" in self._failed_strategies:
            if (now - self._failed_strategies["direct"]).total_seconds() >= self._strategy_cooldown:
                del self._failed_strategies["direct"]
            elif "direct" in strategies:
                strategies.remove("direct")
        
        return strategies

    def _get_proxy_url(self, strategy: str) -> Optional[str]:
        if strategy == "direct":
            return None
        for proxy in get_all_proxies():
            if proxy["name"] == strategy:
                return proxy["url"]
        return None

    async def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        delay = self._min_request_interval + random.uniform(0.3, 1.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.time()

    async def _init_session(self, strategy: str) -> bool:
        """Initialise une session pour une stratégie donnée."""
        logger.info(f"Init session Vinted (strategy: {strategy})...")
        
        proxy_url = self._get_proxy_url(strategy)
        client_config = {"timeout": 25.0, "follow_redirects": True}
        if proxy_url:
            client_config["proxy"] = proxy_url
        
        try:
            async with httpx.AsyncClient(**client_config) as client:
                await self._rate_limit()
                
                # Étape 1: Accéder à la page d'accueil
                response = await client.get(
                    self.BASE_URL,
                    headers=self._get_headers()
                )
                
                if response.status_code != 200:
                    logger.warning(f"Vinted accès échoué ({strategy}): {response.status_code}")
                    self._failed_strategies[strategy] = datetime.now()
                    return False
                
                cookies = {}
                for cookie in response.cookies.jar:
                    if cookie.value:
                        cookies[cookie.name] = cookie.value
                
                await self._rate_limit()
                
                # Étape 2: Test API
                test_response = await client.get(
                    self.SEARCH_URL,
                    params={"search_text": "nike", "per_page": 1},
                    headers=self._get_headers(cookies=cookies, for_api=True)
                )
                
                for cookie in test_response.cookies.jar:
                    if cookie.value:
                        cookies[cookie.name] = cookie.value
                
                if test_response.status_code == 200:
                    self._sessions[strategy] = {
                        "cookies": cookies,
                        "expires": datetime.now() + timedelta(minutes=20),
                    }
                    logger.info(f"Session Vinted OK ({strategy}) - {len(cookies)} cookies")
                    return True
                else:
                    logger.warning(f"Vinted API test échoué ({strategy}): {test_response.status_code}")
                    self._failed_strategies[strategy] = datetime.now()
                    return False
                    
        except Exception as e:
            logger.error(f"Erreur init session ({strategy}): {e}")
            self._failed_strategies[strategy] = datetime.now()
            return False

    def _is_session_valid(self, strategy: str) -> bool:
        if strategy not in self._sessions:
            return False
        session = self._sessions[strategy]
        return datetime.now() < session.get("expires", datetime.min)

    async def search_products(
        self, 
        query: str, 
        limit: int = 50, 
        status_ids: Optional[List[int]] = None
    ) -> List[Dict]:
        """Recherche avec rotation automatique entre stratégies."""
        
        strategies = self._get_available_strategies()
        random.shuffle(strategies)  # Randomiser l'ordre
        
        if not strategies:
            logger.warning("Aucune stratégie disponible (toutes en cooldown)")
            # Reset une stratégie au hasard
            if self._failed_strategies:
                key = random.choice(list(self._failed_strategies.keys()))
                del self._failed_strategies[key]
                strategies = [key]
        
        for strategy in strategies:
            # Init session si nécessaire
            if not self._is_session_valid(strategy):
                if not await self._init_session(strategy):
                    continue
            
            session = self._sessions.get(strategy, {})
            cookies = session.get("cookies", {})
            proxy_url = self._get_proxy_url(strategy)
            
            client_config = {"timeout": 25.0, "follow_redirects": True}
            if proxy_url:
                client_config["proxy"] = proxy_url
            
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
                
                async with httpx.AsyncClient(**client_config) as client:
                    response = await client.get(
                        self.SEARCH_URL,
                        params=params,
                        headers=self._get_headers(cookies=cookies, for_api=True)
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        items = data.get("items", [])
                        logger.debug(f"Vinted ({strategy}): {len(items)} résultats pour '{query}'")
                        return items
                    
                    elif response.status_code in (401, 403, 429):
                        logger.warning(f"Vinted {response.status_code} ({strategy}) - switch stratégie")
                        self._failed_strategies[strategy] = datetime.now()
                        if strategy in self._sessions:
                            del self._sessions[strategy]
                        continue
                    
                    else:
                        logger.warning(f"Vinted error ({strategy}): {response.status_code}")
                        continue
                        
            except Exception as e:
                logger.error(f"Erreur recherche ({strategy}): {e}")
                self._failed_strategies[strategy] = datetime.now()
                continue
        
        logger.warning(f"Toutes les stratégies ont échoué pour '{query}'")
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
                    return float(total_price.get("amount", 0))
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
        """Stats hybrides: neufs d'abord, puis mix avec coefficient."""
        query = self._build_search_query(product_name, brand)
        
        # Chercher articles neufs
        new_items = await self.search_products(query, limit=50, status_ids=[VINTED_STATUS_IDS["new_with_tags"]])
        
        new_prices = [self._extract_price(i) for i in new_items]
        new_prices = [p for p in new_prices if p and p >= 5]
        
        if len(new_prices) >= 5:
            logger.info(f"Vinted: {len(new_prices)} articles NEUFS pour '{query}'")
            return self._build_stats(new_prices, new_items, query, "new", 1.0)
        
        # Fallback: tous articles
        all_items = await self.search_products(query, limit=50)
        all_prices = [self._extract_price(i) for i in all_items]
        all_prices = [p for p in all_prices if p and p >= 5]
        
        if not all_prices:
            return {"nb_listings": 0, "prices": [], "query_used": query, "source_type": "none", "coefficient": 1.0}
        
        coefficient = CONDITION_COEFFICIENTS["mixed"]
        logger.info(f"Vinted: {len(all_prices)} articles MIX pour '{query}' (coef x{coefficient})")
        return self._build_stats(all_prices, all_items, query, "mixed", coefficient)

    def _build_search_query(self, product_name: str, brand: Optional[str] = None) -> str:
        clean_name = re.sub(r'[^\w\s-]', ' ', product_name)
        keywords = []
        
        if brand and brand.lower() not in clean_name.lower():
            keywords.append(brand.lower())
        
        keep_words = {'air', 'force', 'max', 'dunk', 'jordan', '1', '90', '95', '97', 
                      'gel', '1130', '2002', 'samba', 'spezial', 'gazelle', 'campus',
                      'old', 'skool', 'sk8', '550', '530', '9060', 'p6000', 'p-6000'}
        stop_words = {'le', 'la', 'les', 'de', 'du', 'des', 'un', 'une', 'pour',
                      'homme', 'femme', 'men', 'women', 'size', 'taille', 'new', 'neuf',
                      'chaussures', 'shoes', 'sneakers', 'basket', 'baskets', 'amp', '039',
                      'marron', 'noir', 'blanc', 'gris', 'bleu', 'rouge', 'vert', 'rose'}
        
        for word in clean_name.lower().split():
            word = word.strip('-')
            if len(word) > 1 and word not in stop_words and word not in keywords:
                if word in keep_words or len(word) > 2:
                    keywords.append(word)
        
        return " ".join(keywords[:5])

    def _build_stats(self, prices: List[float], items: List[Dict], query: str, source_type: str, coefficient: float) -> Dict[str, Any]:
        if not prices:
            return {"nb_listings": len(items), "prices": [], "query_used": query, "source_type": source_type, "coefficient": coefficient}
        
        initial_median = statistics.median(prices)
        filtered = [p for p in prices if p <= initial_median * 3]
        if len(filtered) < 3:
            filtered = prices
        
        sorted_p = sorted(filtered)
        n = len(filtered)
        
        raw_median = statistics.median(filtered)
        raw_avg = statistics.mean(filtered)
        p25_idx, p75_idx = max(0, int(n * 0.25) - 1), min(n - 1, int(n * 0.75))
        
        sample = []
        for item in items[:5]:
            photo = item.get("photo", {}) or {}
            sample.append({
                "title": item.get("title", ""),
                "price": self._extract_price(item),
                "url": f"{self.BASE_URL}/items/{item.get('id', '')}",
                "photo_url": photo.get("url", "") if isinstance(photo, dict) else "",
                "status": self._extract_item_status(item),
            })
        
        stats = {
            "nb_listings": len(items),
            "prices": filtered,
            "query_used": query,
            "source_type": source_type,
            "coefficient": coefficient,
            "price_min": round(min(filtered) * coefficient, 2),
            "price_max": round(max(filtered) * coefficient, 2),
            "price_avg": round(raw_avg * coefficient, 2),
            "price_median": round(raw_median * coefficient, 2),
            "price_p25": round(sorted_p[p25_idx] * coefficient, 2),
            "price_p75": round(sorted_p[p75_idx] * coefficient, 2),
            "price_median_raw": round(raw_median, 2),
            "sample_listings": sample,
        }
        
        if n >= 2:
            stats["price_std"] = round(statistics.stdev(filtered), 2)
            stats["coefficient_variation"] = round(stats["price_std"] / raw_avg * 100, 1) if raw_avg > 0 else 0
        
        return stats

    async def get_market_stats(self, product_name: str, brand: Optional[str] = None, expected_price: Optional[float] = None) -> Dict[str, Any]:
        return await self.get_market_stats_hybrid(product_name, brand, expected_price)

    def calculate_margin(self, buy_price: float, vinted_stats: Dict, use_percentile: str = "p25") -> Tuple[float, float]:
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
        net = sell_price - vinted_fees - shipping
        margin_euro = net - buy_price
        margin_pct = (margin_euro / buy_price * 100) if buy_price > 0 else 0
        return round(margin_euro, 2), round(margin_pct, 1)

    def calculate_liquidity_score(self, vinted_stats: Dict) -> float:
        nb = vinted_stats.get("nb_listings", 0)
        if nb == 0:
            return 0.0
        
        if nb >= 50: ls = 60
        elif nb >= 30: ls = 50
        elif nb >= 15: ls = 40
        elif nb >= 5: ls = 25
        else: ls = nb * 4
        
        cv = vinted_stats.get("coefficient_variation", 100)
        if cv <= 15: ds = 40
        elif cv <= 25: ds = 30
        elif cv <= 40: ds = 20
        elif cv <= 60: ds = 10
        else: ds = 0
        
        return round(min(ls + ds, 100), 1)


vinted_service = VintedService()


async def get_vinted_stats_for_deal(product_name: str, brand: Optional[str], sale_price: float) -> Dict[str, Any]:
    from app.services.ai_extraction_service import extract_product_name_ai
    
    extraction = await extract_product_name_ai(product_name, brand)
    search_query = extraction.get("search_query", product_name)
    extracted_brand = extraction.get("brand", brand)
    
    logger.info(f"Vinted search: '{product_name[:40]}' -> '{search_query}' (method: {extraction.get('method')})")
    
    stats = await vinted_service.get_market_stats_hybrid(search_query, extracted_brand, sale_price * 2)
    
    if stats.get("nb_listings", 0) == 0:
        return {
            "nb_listings": 0, "margin_euro": 0, "margin_pct": 0, "liquidity_score": 0,
            "sample_listings": [], "source_type": "none", "coefficient": 1.0,
            "extraction_method": extraction.get("method"), "search_query": search_query,
        }
    
    margin_euro, margin_pct = vinted_service.calculate_margin(sale_price, stats, "p25")
    liquidity = vinted_service.calculate_liquidity_score(stats)
    
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
        "liquidity_score": liquidity,
        "sample_listings": stats.get("sample_listings", []),
        "search_query": search_query,
        "source_type": stats.get("source_type", "mixed"),
        "coefficient": stats.get("coefficient", 1.0),
        "extraction_method": extraction.get("method"),
    }
