import os
import json
import requests
import redis
from app.utils.ratelimit import allow
from app.utils.retry import with_retry

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

class CollectorError(Exception):
    pass

def fetch_json(url: str, source: str, limit: int = 30, window_sec: int = 60, cache_ttl: int = 30):
    """
    Fetch JSON from url with:
    - per-source rate limit
    - short cache
    - retries on network/5xx
    """
    r = redis.from_url(REDIS_URL)

    # Cache key (keep it simple)
    cache_key = f"cache:{source}:{url}"
    cached = r.get(cache_key)
    if cached:
        return json.loads(cached), {"cached": True}

    # Rate limit
    if not allow(r, key=source, limit=limit, window_sec=window_sec):
        raise CollectorError(f"Rate limit exceeded for source={source}")

    def _do():
        resp = requests.get(url, timeout=10, headers={
            "Accept": "application/json",
            "User-Agent": "SharktedCollector/1.0 (+contact: admin@sharkted.fr)"
        })
        # Retry on 5xx only, not on 4xx
        if 500 <= resp.status_code < 600:
            raise CollectorError(f"Upstream 5xx: {resp.status_code}")
        resp.raise_for_status()
        return resp.json()

    data = with_retry(_do, retries=3, base_delay=0.5, max_delay=6.0, retry_on=(requests.RequestException, CollectorError))
    r.setex(cache_key, cache_ttl, json.dumps(data))
    return data, {"cached": False}
