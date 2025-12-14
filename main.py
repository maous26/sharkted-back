from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
from rq_scheduler import Scheduler
from datetime import datetime, timedelta, timezone
from rq.job import Job
from app.jobs import test_job, collect_http_json
from app.jobs_adidas import collect_adidas_product
from app.jobs_courir import collect_courir_product
from app.jobs_footlocker import collect_footlocker_product
from app.jobs_size import collect_size_product
from app.jobs_jdsports import collect_jdsports_product

from rq import Queue
import redis
import os
import time

from app.routers.auth import router as auth_router
from app.routers.deals import router as deals_router
from app.routers.sources import router as sources_router
from app.routers.collect import router as collect_router
from app.routers.alerts import router as alerts_router
from app.core.config import JWT_SECRET, JWT_ALGO
from app.services.deal_service import (
    get_deal,
    get_deals_by_source,
    get_source_stats,
    get_all_deals,
    get_recent_deals,
)
from app.core.source_policy import (
    get_policy,
    get_all_source_metrics,
    get_source_metrics,
    unblock_source,
    pick_queue,
    CollectMode,
    SOURCE_POLICIES,
)
from app.core.logging import get_logger, set_trace_id

logger = get_logger(__name__)

# =============================================================================
# APP CONFIGURATION
# =============================================================================

API_VERSION = "1.0.0"
API_TITLE = "Sharkted API"

app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description="Deal collection & aggregation API",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Configuration
ALLOWED_ORIGINS = [
    "https://sharkted-front-production.up.railway.app",
    "https://sharkted.fr",
    "https://www.sharkted.fr",
    "http://localhost:3000",
    "http://localhost:3001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time"],
)


# =============================================================================
# MIDDLEWARE - Request tracking & timing
# =============================================================================

@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Add trace_id and timing to all requests."""
    trace_id = set_trace_id()
    start_time = time.perf_counter()

    # Debug CORS - log Origin header for all requests
    origin = request.headers.get("origin", "NO_ORIGIN")
    logger.info(f"CORS_DEBUG method={request.method} origin={origin} path={request.url.path} headers={dict(request.headers)}")

    response = await call_next(request)

    duration_ms = (time.perf_counter() - start_time) * 1000
    response.headers["X-Request-ID"] = trace_id
    response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

    # Log request (skip health checks)
    if request.url.path != "/health":
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )

    return response


bearer = HTTPBearer(auto_error=False)
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_conn = redis.from_url(REDIS_URL)
queue_high = Queue("high", connection=redis_conn)
queue_default = Queue("default", connection=redis_conn)
queue_low = Queue("low", connection=redis_conn)

# =============================================================================
# SYSTEM ENDPOINTS - Health & Info
# =============================================================================

@app.get("/health")
def health():
    """Health check endpoint for load balancers & monitoring."""
    return {"status": "ok"}


@app.get("/debug/headers")
async def debug_headers(request: Request):
    """Debug endpoint to see what headers the API receives."""
    return {
        "headers": dict(request.headers),
        "method": request.method,
        "url": str(request.url),
        "client": request.client.host if request.client else None,
    }


@app.get("/v1/info")
def api_info():
    """API version and status information."""
    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "status": "operational",
        "sources": {
            "active": len([s for s, p in SOURCE_POLICIES.items() if p.enabled]),
            "total": len(SOURCE_POLICIES),
        },
    }


@app.post("/jobs/schedule/test")
def schedule_test_every_2min():
    scheduler = Scheduler(connection=redis_conn, queue_name="default")
    # Démarre dans 10 secondes
    first_run = datetime.now(timezone.utc) + timedelta(seconds=10)

    job = scheduler.schedule(
        scheduled_time=first_run,
        func=test_job,
        args=["scheduled hello"],
        interval=120,   # toutes les 2 m        repeat=None,
        result_ttl=3600
    )
    return {"scheduled_job_id": job.id, "first_run_utc": first_run.isoformat(), "interval_sec": 120}


@app.post("/jobs/collect/json")
def enqueue_collect_json(
    url: str,
    source: str,
    priority: str = "default",
):
    queues = {
        "high": queue_high,
        "default": queue_default,
        "low": queue_low,
    }

    queue = queues.get(priority, queue_default)

    job = queue.enqueue(
        collect_http_json,
        url,
        source,
        job_timeout=120,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "status": "enqueued",
    }



@app.post("/jobs/test")
def enqueue_test_job():
    job = queue_default.enqueue(test_job, "hello from API", job_timeout=60)

    return {
        "job_id": job.id,
        "status": "enqueued"
    }

# =============================================================================
# ROUTERS - Versioned API (v1)
# =============================================================================

app.include_router(auth_router)
app.include_router(deals_router)      # /v1/deals/*
app.include_router(sources_router)    # /v1/sources/*
app.include_router(collect_router)    # /v1/collect/*
app.include_router(alerts_router)     # /v1/alerts/*

def get_user_from_creds(creds):
    """
    Version MVP: retourne un dict minimal.
    Idéalement, tu remplaces par une vraie lecture DB (user.plan / is_premium).
    """
    if not creds:
        return None

    # Si ton token JWT contient déjà un claim "is_premium", on le lit.
    # Sinon, retourne un user non premium.
    try:
        token = creds.credentials
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {
            "user_id": payload.get("sub") or payload.get("user_id"),
            "is_premium": bool(payload.get("is_premium", False)),
        }
    except Exception:
        return {"is_premium": False}

@app.get("/me")
def me(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Missing token")

    token = creds.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return {"email": payload.get("sub")}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.post("/collect/adidas/product")
def enqueue_collect_adidas_product(
    url: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # logique simple et lisible
    if user.get("is_premium"):
        queue = queue_high
    else:
        queue = queue_low

    job = queue.enqueue(
        collect_adidas_product,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": "adidas",
        "status": "enqueued",
    }


@app.post("/collect/courir/product")
def enqueue_collect_courir_product(
    url: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.get("is_premium"):
        queue = queue_high
    else:
        queue = queue_low

    job = queue.enqueue(
        collect_courir_product,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": "courir",
        "status": "enqueued",
    }


@app.post("/collect/footlocker/product")
def enqueue_collect_footlocker_product(
    url: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.get("is_premium"):
        queue = queue_high
    else:
        queue = queue_low

    job = queue.enqueue(
        collect_footlocker_product,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": "footlocker",
        "status": "enqueued",
    }


@app.post("/collect/size/product")
def enqueue_collect_size_product(
    url: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit Size UK (sneakers)."""
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    queue = queue_high if user.get("is_premium") else queue_low

    job = queue.enqueue(
        collect_size_product,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": "size",
        "status": "enqueued",
    }


@app.post("/collect/jdsports/product")
def enqueue_collect_jdsports_product(
    url: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
):
    """Collecte un produit JD Sports FR (sneakers)."""
    user = get_user_from_creds(creds)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    queue = queue_high if user.get("is_premium") else queue_low

    job = queue.enqueue(
        collect_jdsports_product,
        url,
        job_timeout=120,
        result_ttl=3600,
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "queue": queue.name,
        "source": "jdsports",
        "status": "enqueued",
    }


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    job = Job.fetch(job_id, connection=redis_conn)
    payload = {
        "id": job.id,
        "status": job.get_status(),
        "enqueued_at": str(job.enqueued_at) if job.enqueued_at else None,
        "started_at": str(job.started_at) if job.started_at else None,
        "ended_at": str(job.ended_at) if job.ended_at else None,
    }
    if job.is_finished:
        payload["result"] = job.result
    if job.is_failed:
        payload["error"] = str(job.exc_info)[-800:] if job.exc_info else "unknown"
    return payload


# =============================================================================
# LEGACY ENDPOINTS - Backward compatibility (use /v1/* instead)
# =============================================================================

@app.get("/deals", deprecated=True, tags=["legacy"])
def list_all_deals(
    limit: int = 100,
    offset: int = 0,
    source: str = None,
    min_price: float = None,
    max_price: float = None,
    currency: str = None,
    sort_by: str = "last_seen_at",
    sort_order: str = "desc",
):
    """
    Liste tous les deals avec filtres et tri.

    - **sort_by**: price, last_seen_at, first_seen_at
    - **sort_order**: asc, desc
    - **currency**: EUR, GBP, USD
    """
    return get_all_deals(
        limit=limit,
        offset=offset,
        source=source,
        min_price=min_price,
        max_price=max_price,
        currency=currency,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.get("/deals/recent", deprecated=True, tags=["legacy"])
def list_recent_deals(hours: int = 24, limit: int = 50):
    """[DEPRECATED: use /v1/deals/recent] Deals vus dans les dernières X heures."""
    deals = get_recent_deals(hours=hours, limit=limit)
    return {"count": len(deals), "hours": hours, "deals": deals}


@app.get("/deals/stats", deprecated=True, tags=["legacy"])
def deals_stats():
    """[DEPRECATED: use /v1/deals/stats] Statistiques par source."""
    return get_source_stats()


@app.get("/deals/{source}", deprecated=True, tags=["legacy"])
def list_deals_by_source(
    source: str,
    limit: int = 100,
    offset: int = 0,
    min_price: float = None,
    max_price: float = None,
):
    """Liste les deals d'une source avec filtres optionnels."""
    deals = get_deals_by_source(
        source=source,
        limit=limit,
        offset=offset,
        min_price=min_price,
        max_price=max_price,
    )
    return {"source": source, "count": len(deals), "deals": deals}


@app.get("/deals/{source}/{external_id}", deprecated=True, tags=["legacy"])
def get_deal_detail(source: str, external_id: str):
    """[DEPRECATED: use /v1/deals/{source}/{external_id}] Récupère un deal spécifique."""
    deal = get_deal(source, external_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@app.get("/sources/status", deprecated=True, tags=["legacy"])
def get_sources_status():
    """
    État de toutes les sources configurées.
    Retourne les métriques, mode actuel, blocages, etc.
    """
    metrics = get_all_source_metrics()
    result = {}

    for source, m in metrics.items():
        policy = get_policy(source)
        result[source] = {
            "source": source,
            "enabled": policy.enabled,
            "configured_mode": policy.mode.value,
            "current_mode": m.current_mode.value,
            "allow_proxy": policy.allow_proxy,
            "allow_browser": policy.allow_browser,
            "total_attempts": m.total_attempts,
            "total_success": m.total_success,
            "total_failures": m.total_failures,
            "success_rate_24h": m.success_rate_24h,
            "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error_at": m.last_error_at.isoformat() if m.last_error_at else None,
            "last_error_type": m.last_error_type,
            "last_status_code": m.last_status_code,
            "is_blocked": m.is_blocked,
            "blocked_until": m.blocked_until.isoformat() if m.blocked_until else None,
            "consecutive_failures": m.consecutive_failures,
        }

    return {"sources": result, "count": len(result)}


@app.get("/sources/{source}/status", deprecated=True, tags=["legacy"])
def get_source_status(source: str):
    """[DEPRECATED: use /v1/sources/{source}/status] État détaillé d'une source spécifique."""
    if source not in SOURCE_POLICIES:
        raise HTTPException(status_code=404, detail=f"Source '{source}' not configured")

    policy = get_policy(source)
    m = get_source_metrics(source)

    return {
        "source": source,
        "policy": {
            "mode": policy.mode.value,
            "enabled": policy.enabled,
            "reason": policy.reason,
            "max_retries": policy.max_retries,
            "base_interval_sec": policy.base_interval_sec,
            "allow_proxy": policy.allow_proxy,
            "allow_browser": policy.allow_browser,
        },
        "metrics": {
            "current_mode": m.current_mode.value,
            "total_attempts": m.total_attempts,
            "total_success": m.total_success,
            "total_failures": m.total_failures,
            "success_rate_24h": m.success_rate_24h,
            "last_success_at": m.last_success_at.isoformat() if m.last_success_at else None,
            "last_error_at": m.last_error_at.isoformat() if m.last_error_at else None,
            "last_error_type": m.last_error_type,
            "last_status_code": m.last_status_code,
            "is_blocked": m.is_blocked,
            "blocked_until": m.blocked_until.isoformat() if m.blocked_until else None,
            "consecutive_failures": m.consecutive_failures,
        },
    }


@app.post("/sources/{source}/unblock", deprecated=True, tags=["legacy"])
def unblock_source_endpoint(source: str):
    """[DEPRECATED: use /v1/sources/{source}/unblock] Débloque manuellement une source."""
    if source not in SOURCE_POLICIES:
        raise HTTPException(status_code=404, detail=f"Source '{source}' not configured")

    was_blocked = unblock_source(source)
    m = get_source_metrics(source)

    return {
        "source": source,
        "was_blocked": was_blocked,
        "current_mode": m.current_mode.value,
        "message": f"Source '{source}' unblocked" if was_blocked else f"Source '{source}' was not blocked",
    }
