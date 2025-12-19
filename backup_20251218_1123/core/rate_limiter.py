"""
Rate Limiter - Protection contre les abus.

Utilise Redis pour un rate limiting distribué.
Limites par endpoint type:
- auth/login: 5/minute par IP (brute force)
- collect/*: 10/minute par user, 30/minute par IP
- sources/admin: 10/minute par user
"""
import os
import time
from functools import wraps
from typing import Optional, Callable

from fastapi import HTTPException, Request
import redis

from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_redis_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Get or create Redis connection."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


class RateLimitExceeded(HTTPException):
    """Exception for rate limit exceeded."""

    def __init__(self, retry_after: int = 60):
        super().__init__(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def check_rate_limit(
    key: str,
    max_requests: int,
    window_seconds: int,
) -> tuple[bool, int]:
    """
    Check if rate limit is exceeded using sliding window.

    Args:
        key: Unique key for this limit (e.g., "login:192.168.1.1")
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds

    Returns:
        Tuple of (is_allowed, remaining_requests)
    """
    redis_client = get_redis()
    now = time.time()
    window_start = now - window_seconds

    # Redis key for this rate limit
    redis_key = f"ratelimit:{key}"

    pipe = redis_client.pipeline()

    # Remove old entries
    pipe.zremrangebyscore(redis_key, 0, window_start)

    # Count current entries
    pipe.zcard(redis_key)

    # Add current request
    pipe.zadd(redis_key, {str(now): now})

    # Set expiry
    pipe.expire(redis_key, window_seconds + 1)

    results = pipe.execute()
    current_count = results[1]

    remaining = max(0, max_requests - current_count - 1)
    is_allowed = current_count < max_requests

    return is_allowed, remaining


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, handling proxies."""
    # Check X-Forwarded-For header (set by reverse proxy)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take first IP (client IP)
        return forwarded_for.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback to direct client
    if request.client:
        return request.client.host

    return "unknown"


# =============================================================================
# Rate Limit Configurations
# =============================================================================

RATE_LIMITS = {
    "auth_login": {"max": 5, "window": 60},      # 5/min per IP
    "auth_register": {"max": 3, "window": 60},   # 3/min per IP
    "collect": {"max": 10, "window": 60},        # 10/min per user
    "collect_ip": {"max": 30, "window": 60},     # 30/min per IP
    "sources_admin": {"max": 10, "window": 60},  # 10/min per user
}


def rate_limit_login(request: Request) -> None:
    """Rate limit for login endpoint."""
    client_ip = get_client_ip(request)
    config = RATE_LIMITS["auth_login"]

    allowed, remaining = check_rate_limit(
        key=f"login:{client_ip}",
        max_requests=config["max"],
        window_seconds=config["window"],
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded on login",
            ip=client_ip,
            endpoint="/auth/login",
        )
        raise RateLimitExceeded(retry_after=config["window"])


def rate_limit_register(request: Request) -> None:
    """Rate limit for register endpoint."""
    client_ip = get_client_ip(request)
    config = RATE_LIMITS["auth_register"]

    allowed, remaining = check_rate_limit(
        key=f"register:{client_ip}",
        max_requests=config["max"],
        window_seconds=config["window"],
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded on register",
            ip=client_ip,
            endpoint="/auth/register",
        )
        raise RateLimitExceeded(retry_after=config["window"])


def rate_limit_collect(request: Request, user_id: Optional[str] = None) -> None:
    """Rate limit for collect endpoints."""
    client_ip = get_client_ip(request)

    # Per-IP limit
    config_ip = RATE_LIMITS["collect_ip"]
    allowed_ip, _ = check_rate_limit(
        key=f"collect:ip:{client_ip}",
        max_requests=config_ip["max"],
        window_seconds=config_ip["window"],
    )

    if not allowed_ip:
        logger.warning(
            "Rate limit exceeded on collect (IP)",
            ip=client_ip,
        )
        raise RateLimitExceeded(retry_after=config_ip["window"])

    # Per-user limit (if authenticated)
    if user_id:
        config_user = RATE_LIMITS["collect"]
        allowed_user, _ = check_rate_limit(
            key=f"collect:user:{user_id}",
            max_requests=config_user["max"],
            window_seconds=config_user["window"],
        )

        if not allowed_user:
            logger.warning(
                "Rate limit exceeded on collect (user)",
                user_id=user_id,
            )
            raise RateLimitExceeded(retry_after=config_user["window"])


def rate_limit_sources_admin(request: Request, user_id: str) -> None:
    """Rate limit for sources admin endpoints."""
    config = RATE_LIMITS["sources_admin"]

    allowed, _ = check_rate_limit(
        key=f"sources:admin:{user_id}",
        max_requests=config["max"],
        window_seconds=config["window"],
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded on sources admin",
            user_id=user_id,
        )
        raise RateLimitExceeded(retry_after=config["window"])
