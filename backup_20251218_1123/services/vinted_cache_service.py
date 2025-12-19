"""
Vinted Cache Service - Scraping avec Web Unlocker BrightData.
"""

import json
import hashlib
import random
import time
import re
import ssl
from datetime import datetime
from typing import Dict, Any, List, Optional
import statistics

import httpx
import redis
from loguru import logger

from app.services.proxy_service import get_web_unlocker_proxy, has_web_unlocker_configured

REDIS_URL = "redis://redis:6379/0"
CACHE_TTL = 3600


def get_redis():
    return redis.from_url(REDIS_URL)


def get_cache_key(query: str) -> str:
    normalized = query.lower().strip()
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    normalized = ' '.join(normalized.split())
    hash_key = hashlib.md5(normalized.encode()).hexdigest()[:12]
    return f"vinted:stats:{hash_key}"


def get_cached_stats(query: str) -> Optional[Dict]:
    try:
        r = get_redis()
        key = get_cache_key(query)
        data = r.get(key)
        if data:
            stats = json.loads(data)
            logger.debug(f"Cache HIT for '{query[:30]}'")
            return stats
        logger.debug(f"Cache MISS for '{query[:30]}'")
        return None
    except Exception as e:
        logger.warning(f"Redis cache error: {e}")
        return None


def set_cached_stats(query: str, stats: Dict, ttl: int = CACHE_TTL):
    try:
        r = get_redis()
        key = get_cache_key(query)
        r.setex(key, ttl, json.dumps(stats))
        logger.debug(f"Cached stats for '{query[:30]}'")
    except Exception as e:
        logger.warning(f"Redis cache set error: {e}")


class VintedBatchScraper:
    BASE_URL = "https://www.vinted.fr"
    SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"
    
    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]
    
    def __init__(self):
        self._cookies = None
        self._last_request = 0
        self._min_delay = 2
        self._use_proxy = has_web_unlocker_configured()
    
    def _get_headers(self, for_api: bool = False) -> Dict:
        headers = {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
        }
        if for_api:
            headers["Referer"] = self.BASE_URL
        return headers
    
    def _get_client(self) -> httpx.Client:
        proxy_url = None
        if self._use_proxy:
            proxy_config = get_web_unlocker_proxy()
            if proxy_config:
                proxy_url = proxy_config.get("http")
                logger.debug("Using Web Unlocker proxy for Vinted")
        
        # Désactiver vérification SSL pour le proxy
        return httpx.Client(
            timeout=30,
            follow_redirects=True,
            proxy=proxy_url,
            verify=False if proxy_url else True
        )
    
    def _init_session(self) -> bool:
        try:
            elapsed = time.time() - self._last_request
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)
            
            with self._get_client() as client:
                resp = client.get(self.BASE_URL, headers=self._get_headers())
                self._last_request = time.time()
                
                if resp.status_code == 200:
                    self._cookies = dict(resp.cookies)
                    logger.info(f"Vinted session initialized (proxy={self._use_proxy})")
                    return True
                else:
                    logger.warning(f"Vinted init failed: {resp.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Vinted session error: {e}")
            return False
    
    def search(self, query: str, limit: int = 20) -> Optional[Dict]:
        cached = get_cached_stats(query)
        if cached:
            return cached
        
        if not self._cookies:
            if not self._init_session():
                return None
        
        elapsed = time.time() - self._last_request
        if elapsed < self._min_delay:
            time.sleep(self._min_delay - elapsed)
        
        try:
            params = {
                "search_text": query,
                "per_page": limit,
                "order": "newest_first",
            }
            
            with self._get_client() as client:
                if self._cookies:
                    client.cookies.update(self._cookies)
                
                resp = client.get(self.SEARCH_URL, params=params, headers=self._get_headers(for_api=True))
                self._last_request = time.time()
                
                if resp.status_code == 403:
                    logger.warning("Vinted 403 - resetting session")
                    self._cookies = None
                    if self._init_session():
                        client.cookies.update(self._cookies)
                        resp = client.get(self.SEARCH_URL, params=params, headers=self._get_headers(for_api=True))
                
                if resp.status_code != 200:
                    logger.warning(f"Vinted search failed: {resp.status_code}")
                    return None
                
                data = resp.json()
                items = data.get("items", [])
                
                if not items:
                    stats = {"nb_listings": 0, "query_used": query}
                    set_cached_stats(query, stats)
                    return stats
                
                stats = self._calculate_stats(items, query)
                set_cached_stats(query, stats)
                return stats
                
        except Exception as e:
            logger.error(f"Vinted search error: {e}")
            return None
    
    def _calculate_stats(self, items: List[Dict], query: str) -> Dict:
        prices = []
        sample_listings = []
        
        for item in items[:20]:
            try:
                price = float(item.get("price", {}).get("amount", 0))
                if price > 0:
                    prices.append(price)
                    if len(sample_listings) < 5:
                        sample_listings.append({
                            "title": item.get("title", "")[:50],
                            "price": price,
                            "url": item.get("url", ""),
                            "photo_url": item.get("photo", {}).get("url", ""),
                        })
            except (TypeError, ValueError):
                continue
        
        if not prices:
            return {"nb_listings": 0, "query_used": query}
        
        prices.sort()
        n = len(prices)
        
        stats = {
            "nb_listings": n,
            "price_min": prices[0],
            "price_max": prices[-1],
            "price_avg": round(sum(prices) / n, 2),
            "price_median": round(statistics.median(prices), 2),
            "query_used": query,
            "sample_listings": sample_listings,
            "fetched_at": datetime.utcnow().isoformat(),
        }
        
        if n >= 4:
            stats["price_p25"] = round(prices[n // 4], 2)
            stats["price_p75"] = round(prices[(3 * n) // 4], 2)
        
        if stats["price_avg"] > 0:
            std_dev = statistics.stdev(prices) if n > 1 else 0
            stats["coefficient_variation"] = round((std_dev / stats["price_avg"]) * 100, 1)
        
        stats["liquidity_score"] = min(100, n * 5)
        
        return stats


_scraper = None

def get_scraper() -> VintedBatchScraper:
    global _scraper
    if _scraper is None:
        _scraper = VintedBatchScraper()
    return _scraper
