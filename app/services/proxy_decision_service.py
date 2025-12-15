"""
Proxy Decision Service - Intelligent Web Unlocker usage based on business value.

RULE OF GOLD:
Web Unlocker is NEVER used "to know".
It is used "because someone PAYS to know".

Cost structure (BrightData Web Unlocker):
- Standard domains: $1.50/1k requests = $0.0015/req
- Premium domains: $2.50/1k requests = $0.0025/req
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.user import User

logger = get_logger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class SiteProtection(str, Enum):
    """Site protection level."""
    NONE = "none"           # No protection, standard scraping works
    BASIC = "basic"         # Simple rate limiting, rotating proxy works
    PREMIUM = "premium"     # Cloudflare/advanced, needs Web Unlocker


# Sites that REQUIRE Web Unlocker (protected by Cloudflare or similar)
PREMIUM_PROTECTED_SITES = {
    "nike": SiteProtection.PREMIUM,
    "adidas": SiteProtection.PREMIUM,
    "zalando": SiteProtection.PREMIUM,
    "printemps": SiteProtection.PREMIUM,
    "galerieslafayette": SiteProtection.PREMIUM,
    "laredoute": SiteProtection.PREMIUM,
    "vinted": SiteProtection.PREMIUM,  # For API access
    "sns": SiteProtection.PREMIUM,     # Sneakersnstuff
    "footlocker": SiteProtection.BASIC,
    "jdsports": SiteProtection.BASIC,
}

# Sites that work WITHOUT proxy (free to scrape)
FREE_SITES = {
    "courir",
    "asphaltgold",
    "solebox",
    "kith",
    "size",
}

# Cost per request (in EUR, approximate)
COST_PER_REQUEST = {
    "standard": 0.0013,   # ~$1.50/1k
    "premium": 0.0022,    # ~$2.50/1k
}

# Score threshold for auto-triggering Web Unlocker
SCORE_THRESHOLD_HIGH = 80

# Max requests per Premium user per day (soft limit for cost control)
MAX_REQUESTS_PER_PREMIUM_USER_DAY = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ProxyDecision:
    """Result of proxy decision."""
    use_web_unlocker: bool
    reason: str
    estimated_cost: float
    trigger_type: str  # "alert", "high_score", "premium_refresh", "denied"
    served_users: int
    site: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_web_unlocker": self.use_web_unlocker,
            "reason": self.reason,
            "estimated_cost": self.estimated_cost,
            "trigger_type": self.trigger_type,
            "served_users": self.served_users,
            "site": self.site,
        }


@dataclass
class ProxyUsageLog:
    """Log entry for proxy usage tracking."""
    timestamp: datetime
    site: str
    url: str
    trigger_type: str
    cost_estimate: float
    served_users: int
    success: bool
    response_code: Optional[int]
    duration_ms: Optional[float]


# =============================================================================
# CORE DECISION FUNCTIONS
# =============================================================================

def get_active_premium_count() -> int:
    """Count active Premium users."""
    session = SessionLocal()
    try:
        count = session.query(User).filter(
            User.plan.in_(["premium", "pro", "agency", "owner"])
        ).count()
        return count
    except Exception as e:
        logger.error(f"Error counting premium users: {e}")
        return 0
    finally:
        session.close()


def get_premium_users_interested_in(brand: str = None, model: str = None) -> int:
    """
    Count Premium users potentially interested in this product.
    Used for mutualisation calculation.
    
    TODO: Implement based on user alerts/preferences
    For now, returns all Premium users.
    """
    return get_active_premium_count()


def get_site_protection_level(site: str) -> SiteProtection:
    """Get protection level for a site."""
    site_lower = site.lower()
    if site_lower in PREMIUM_PROTECTED_SITES:
        return PREMIUM_PROTECTED_SITES[site_lower]
    if site_lower in FREE_SITES:
        return SiteProtection.NONE
    return SiteProtection.BASIC


def should_use_web_unlocker(
    site: str,
    product_score: float = 0,
    has_premium_alert: bool = False,
    is_fallback_after_403: bool = False,
    brand: str = None,
    model: str = None,
) -> ProxyDecision:
    """
    MAIN DECISION FUNCTION
    
    Determines if Web Unlocker should be used for a request.
    
    Rules (in order):
    1. No Premium users → DENY
    2. Site not premium-protected → DENY (use standard scraping)
    3. Premium alert exists → ALLOW
    4. High score product → ALLOW
    5. Fallback after 403 on premium site → ALLOW
    6. Otherwise → DENY
    
    Args:
        site: Site slug (e.g., "nike", "zalando")
        product_score: Flip score of the product (0-100)
        has_premium_alert: True if a Premium user has an alert for this
        is_fallback_after_403: True if standard scraping returned 403
        brand: Product brand (for mutualisation)
        model: Product model (for mutualisation)
    
    Returns:
        ProxyDecision with use_web_unlocker, reason, cost, etc.
    """
    site_lower = site.lower()
    protection = get_site_protection_level(site_lower)
    premium_count = get_active_premium_count()
    served_users = get_premium_users_interested_in(brand, model)
    
    # Cost estimation
    is_premium_domain = site_lower in ["nike", "adidas", "zalando"]
    cost_type = "premium" if is_premium_domain else "standard"
    estimated_cost = COST_PER_REQUEST[cost_type]
    
    # ==========================================================================
    # RULE 1: No Premium users → DENY
    # ==========================================================================
    if premium_count == 0:
        return ProxyDecision(
            use_web_unlocker=False,
            reason="No active Premium users - Web Unlocker disabled",
            estimated_cost=0,
            trigger_type="denied",
            served_users=0,
            site=site_lower,
        )
    
    # ==========================================================================
    # RULE 2: Site not premium-protected → DENY
    # ==========================================================================
    if protection != SiteProtection.PREMIUM:
        return ProxyDecision(
            use_web_unlocker=False,
            reason=f"Site '{site}' protection={protection.value} - standard scraping sufficient",
            estimated_cost=0,
            trigger_type="denied",
            served_users=0,
            site=site_lower,
        )
    
    # ==========================================================================
    # RULE 3: Premium alert exists → ALLOW
    # ==========================================================================
    if has_premium_alert:
        logger.info(f"Web Unlocker ALLOWED: Premium alert for {site}")
        return ProxyDecision(
            use_web_unlocker=True,
            reason="Premium user alert - request justified",
            estimated_cost=estimated_cost,
            trigger_type="alert",
            served_users=served_users,
            site=site_lower,
        )
    
    # ==========================================================================
    # RULE 4: High score product → ALLOW
    # ==========================================================================
    if product_score >= SCORE_THRESHOLD_HIGH:
        logger.info(f"Web Unlocker ALLOWED: High score ({product_score}) for {site}")
        return ProxyDecision(
            use_web_unlocker=True,
            reason=f"High flip score ({product_score}) - valuable product",
            estimated_cost=estimated_cost,
            trigger_type="high_score",
            served_users=served_users,
            site=site_lower,
        )
    
    # ==========================================================================
    # RULE 5: Fallback after 403 on premium site → ALLOW (with caution)
    # ==========================================================================
    if is_fallback_after_403:
        logger.info(f"Web Unlocker ALLOWED: Fallback after 403 for {site}")
        return ProxyDecision(
            use_web_unlocker=True,
            reason="Fallback after 403 - standard scraping blocked",
            estimated_cost=estimated_cost,
            trigger_type="fallback_403",
            served_users=served_users,
            site=site_lower,
        )
    
    # ==========================================================================
    # DEFAULT: DENY
    # ==========================================================================
    return ProxyDecision(
        use_web_unlocker=False,
        reason=f"No trigger condition met (score={product_score}, no alert)",
        estimated_cost=0,
        trigger_type="denied",
        served_users=0,
        site=site_lower,
    )


# =============================================================================
# USAGE TRACKING
# =============================================================================

# In-memory log (TODO: persist to database)
_usage_log: List[ProxyUsageLog] = []
_daily_costs: Dict[str, float] = {}  # date -> total cost


def log_proxy_usage(
    site: str,
    url: str,
    decision: ProxyDecision,
    success: bool,
    response_code: int = None,
    duration_ms: float = None,
):
    """Log a Web Unlocker request for cost tracking."""
    if not decision.use_web_unlocker:
        return  # Only log actual usage
    
    entry = ProxyUsageLog(
        timestamp=datetime.utcnow(),
        site=site,
        url=url,
        trigger_type=decision.trigger_type,
        cost_estimate=decision.estimated_cost,
        served_users=decision.served_users,
        success=success,
        response_code=response_code,
        duration_ms=duration_ms,
    )
    _usage_log.append(entry)
    
    # Track daily costs
    date_key = entry.timestamp.strftime("%Y-%m-%d")
    if date_key not in _daily_costs:
        _daily_costs[date_key] = 0
    _daily_costs[date_key] += decision.estimated_cost
    
    logger.info(
        f"Web Unlocker usage: site={site} trigger={decision.trigger_type} "
        f"cost={decision.estimated_cost:.4f}€ served={decision.served_users} "
        f"success={success}"
    )


def get_usage_stats(days: int = 7) -> Dict[str, Any]:
    """Get Web Unlocker usage statistics."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent_logs = [l for l in _usage_log if l.timestamp >= cutoff]
    
    total_requests = len(recent_logs)
    total_cost = sum(l.cost_estimate for l in recent_logs)
    success_count = sum(1 for l in recent_logs if l.success)
    
    # By trigger type
    by_trigger = {}
    for log in recent_logs:
        if log.trigger_type not in by_trigger:
            by_trigger[log.trigger_type] = {"count": 0, "cost": 0}
        by_trigger[log.trigger_type]["count"] += 1
        by_trigger[log.trigger_type]["cost"] += log.cost_estimate
    
    # By site
    by_site = {}
    for log in recent_logs:
        if log.site not in by_site:
            by_site[log.site] = {"count": 0, "cost": 0}
        by_site[log.site]["count"] += 1
        by_site[log.site]["cost"] += log.cost_estimate
    
    # Daily breakdown
    daily = {}
    for log in recent_logs:
        date_key = log.timestamp.strftime("%Y-%m-%d")
        if date_key not in daily:
            daily[date_key] = {"count": 0, "cost": 0}
        daily[date_key]["count"] += 1
        daily[date_key]["cost"] += log.cost_estimate
    
    return {
        "period_days": days,
        "total_requests": total_requests,
        "total_cost_eur": round(total_cost, 4),
        "success_rate": round(success_count / max(total_requests, 1) * 100, 1),
        "avg_cost_per_request": round(total_cost / max(total_requests, 1), 4),
        "by_trigger": by_trigger,
        "by_site": by_site,
        "daily": daily,
        "premium_users_active": get_active_premium_count(),
    }


# =============================================================================
# HELPER FOR SCRAPERS
# =============================================================================

def get_proxy_for_site(site: str) -> Optional[Dict[str, str]]:
    """
    Get the appropriate proxy configuration for a site.
    
    Returns None if no proxy needed, or proxy config dict.
    """
    from app.models.proxy_settings import ProxySettings
    
    protection = get_site_protection_level(site)
    
    if protection == SiteProtection.NONE:
        return None
    
    session = SessionLocal()
    try:
        if protection == SiteProtection.PREMIUM:
            # Need Web Unlocker
            proxy = session.query(ProxySettings).filter(
                ProxySettings.proxy_type == "web_unlocker",
                ProxySettings.enabled == True,
            ).first()
        else:
            # Basic protection, residential proxy might work
            proxy = session.query(ProxySettings).filter(
                ProxySettings.proxy_type.in_(["residential", "rotating"]),
                ProxySettings.enabled == True,
            ).first()
        
        if proxy:
            return {
                "proxy_url": proxy.get_proxy_url(),
                "host": proxy.host,
                "port": proxy.port,
                "username": proxy.username,
                "password": proxy.password,
            }
        return None
    finally:
        session.close()
