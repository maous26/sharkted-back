"""
Collect Router - Endpoints de collecte de produits.
Endpoints: /v1/collect/*
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
from rq import Queue
import redis
import os

from app.core.config import JWT_SECRET
from app.core.url_validator import validate_url
from app.core.exceptions import ValidationError
from app.core.rate_limiter import rate_limit_collect
from app.jobs_adidas import collect_adidas_product
from app.jobs_courir import collect_courir_product
from app.jobs_footlocker import collect_footlocker_product
from app.jobs_size import collect_size_product
from app.jobs_jdsports import collect_jdsports_product

router = APIRouter(prefix="/v1/collect", tags=["collect"])
bearer = HTTPBearer(auto_error=False)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_conn = redis.from_url(REDIS_URL)
queue_high = Queue("high", connection=redis_conn)
queue_low = Queue("low", connection=redis_conn)


def get_user_from_creds(creds: HTTPAuthorizationCredentials):
    """Extract user info from JWT credentials."""
    if not creds:
        return None

    try:
        token = creds.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {
            "user_id": payload.get("sub") or payload.get("user_id"),
            "is_premium": bool(payload.get("is_premium", False)),
        }
    except Exception:
        return {"is_premium": False}


def _enqueue_job(job_func, url: str, source: str, creds, request: Request):
    """Helper to enqueue a collect job with URL validation and rate limiting."""
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Rate limit: 10/min per user, 30/min per IP
    rate_limit_collect(request, user_id=user.get("user_id"))

    # Anti-SSRF: valider l'URL
    try:
        validated_url = validate_url(url, source)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    queue = queue_high if user.get("is_premium") else queue_low

    job = queue.enqueue(
        job_func,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": source,
        "status": "enqueued",
    }


@router.post("/courir/product")
def enqueue_courir(
    url: str,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit Courir."""
    return _enqueue_job(collect_courir_product, url, "courir", creds, request)


@router.post("/footlocker/product")
def enqueue_footlocker(
    url: str,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit Footlocker FR."""
    return _enqueue_job(collect_footlocker_product, url, "footlocker", creds, request)


@router.post("/size/product")
def enqueue_size(
    url: str,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit Size UK."""
    return _enqueue_job(collect_size_product, url, "size", creds, request)


@router.post("/jdsports/product")
def enqueue_jdsports(
    url: str,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit JD Sports FR."""
    return _enqueue_job(collect_jdsports_product, url, "jdsports", creds, request)


@router.post("/adidas/product")
def enqueue_adidas(
    url: str,
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit Adidas (actuellement bloqué)."""
    return _enqueue_job(collect_adidas_product, url, "adidas", creds, request)


# KITH scraper - bulk collection
from app.jobs_kith import collect_kith_collection, collect_all_kith

@router.post("/kith/collection")
def enqueue_kith_collection(
    collection: str = "footwear-sale",
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte une collection KITH EU (footwear-sale, kids-footwear-sale, etc.)."""
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    queue = queue_high if user.get("is_premium") else queue_low
    job = queue.enqueue(
        collect_kith_collection,
        collection,
        job_timeout=300,
        result_ttl=3600,
    )
    
    return {"job_id": job.id, "queue": queue.name, "source": "kith", "collection": collection, "status": "enqueued"}


@router.post("/kith/all")
def enqueue_kith_all(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte toutes les collections KITH EU (sale + kids)."""
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    queue = queue_high if user.get("is_premium") else queue_low
    job = queue.enqueue(
        collect_all_kith,
        job_timeout=600,
        result_ttl=3600,
    )
    
    return {"job_id": job.id, "queue": queue.name, "source": "kith", "status": "enqueued"}
