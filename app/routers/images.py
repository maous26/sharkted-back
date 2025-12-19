"""
Image Proxy - Contourne la protection hotlinking via Squid local.

Utilise le proxy Squid du serveur (172.18.0.1:3128) pour fetch les images.
"""

import hashlib
import time
from fastapi import APIRouter, HTTPException, Response
import httpx
from loguru import logger

router = APIRouter(prefix="/v1/images", tags=["images"])

# Squid proxy local
SQUID_PROXY = "http://172.18.0.1:3128"

# Cache simple en mémoire (TTL 1h)
_image_cache: dict = {}
_cache_ttl = 3600
_max_cache_size = 200

# User agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]


def _get_cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _cleanup_cache():
    now = time.time()
    expired = [k for k, v in _image_cache.items() if now - v["ts"] > _cache_ttl]
    for k in expired:
        del _image_cache[k]
    if len(_image_cache) > _max_cache_size:
        sorted_keys = sorted(_image_cache.keys(), key=lambda k: _image_cache[k]["ts"])
        for k in sorted_keys[:len(_image_cache) - _max_cache_size]:
            del _image_cache[k]


@router.get("/proxy")
async def proxy_image(url: str):
    """
    Proxy une image via Squid local.
    
    Usage: /v1/images/proxy?url=https://www.courir.com/image.jpg
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL required")
    
    # Domaines autorisés
    allowed_domains = [
        "courir.com", "zalando.", "nike.com", "adidas.",
        "footlocker.", "jdsports.", "size.co.uk", "snipes.",
        "kith.com", "ssense.com", "endclothing.com",
        "demandware.static", "akamaized.net", "cloudfront.net",
        "printemps.com", "media-cdn.printemps.com",
        "laredoute.", "asos.", "footpatrol.", "bstn.",
    ]
    
    if not any(domain in url.lower() for domain in allowed_domains):
        raise HTTPException(status_code=403, detail="Domain not allowed")
    
    # Check cache
    cache_key = _get_cache_key(url)
    if cache_key in _image_cache:
        cached = _image_cache[cache_key]
        if time.time() - cached["ts"] < _cache_ttl:
            return Response(
                content=cached["data"],
                media_type=cached["content_type"],
                headers={"X-Cache": "HIT", "Cache-Control": "public, max-age=3600"}
            )
    
    # Fetch via Squid
    try:
        import random
        ua = random.choice(USER_AGENTS)
        
        # Extraire le domaine pour le Referer
        from urllib.parse import urlparse
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"
        
        headers = {
            "User-Agent": ua,
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Referer": referer,
        }
        
        async with httpx.AsyncClient(
            timeout=15.0, 
            follow_redirects=True,
            proxy=SQUID_PROXY
        ) as client:
            resp = await client.get(url, headers=headers)
        
        if resp.status_code != 200:
            logger.warning(f"Image proxy failed: {url} -> {resp.status_code}")
            raise HTTPException(status_code=resp.status_code, detail="Failed to fetch image")
        
        content_type = resp.headers.get("content-type", "image/jpeg")
        image_data = resp.content
        
        # Cache
        _cleanup_cache()
        _image_cache[cache_key] = {
            "data": image_data,
            "content_type": content_type,
            "ts": time.time(),
        }
        
        return Response(
            content=image_data,
            media_type=content_type,
            headers={
                "X-Cache": "MISS",
                "Cache-Control": "public, max-age=3600",
            }
        )
        
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image fetch timeout")
    except Exception as e:
        logger.error(f"Image proxy error: {url} -> {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_image_cache_stats():
    return {
        "cached_images": len(_image_cache),
        "max_cache_size": _max_cache_size,
        "proxy": "squid://172.18.0.1:3128",
    }
