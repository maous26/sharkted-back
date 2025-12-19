"""
Admin Router - Administration endpoints.
Endpoints: /v1/admin/*
"""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List
from jose import jwt, JWTError
from datetime import datetime

from app.db.session import SessionLocal
from app.core.source_policy import get_all_source_metrics
from app.core.logging import get_logger
from app.core.config import JWT_SECRET, JWT_ALGO
from app.models.user import User
from app.models.proxy_settings import ProxySettings
from app.models.subscription import (
    SubscriptionTier, 
    get_tier_limits, 
    get_tier_sources,
    BASIC_SOURCES, 
    PREMIUM_SOURCES
)

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

# Auth
bearer = HTTPBearer(auto_error=False)


def get_current_admin(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Verify admin access with logging."""
    logger.info(f"get_current_admin called")
    if not creds:
        logger.warning("No credentials provided")
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        logger.info(f"Token decoded for: {email}")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.email == email).first()
            if not user:
                logger.warning(f"User not found: {email}")
                raise HTTPException(status_code=401, detail="User not found")
            is_admin = user.email == "admin@sharkted.fr" or (user.plan or "").lower() in ("pro", "agency", "owner", "premium")
            logger.info(f"User {email} plan={user.plan} is_admin={is_admin}")
            if not is_admin:
                raise HTTPException(status_code=403, detail="Admin access required")
            return {"user_id": user.id, "email": user.email, "is_admin": True}
        finally:
            session.close()
    except JWTError as e:
        logger.error(f"JWT decode error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_admin_with_log(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> dict:
    """Verify admin access by checking the database."""
    if not creds:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Check admin status from database
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.email == email).first()
            if not user:
                raise HTTPException(status_code=401, detail="User not found")
            
            # Check if admin
            is_admin = user.email == "admin@sharkted.fr" or (user.plan or "").lower() in ("pro", "agency", "owner", "premium")
            if not is_admin:
                raise HTTPException(status_code=403, detail="Admin access required")
            
            return {
                "user_id": user.id,
                "email": user.email,
                "is_admin": True,
            }
        finally:
            session.close()
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# =============================================================================
# STATS
# =============================================================================

@router.get("/stats")
def get_admin_stats():
    """Get admin dashboard statistics."""
    session = SessionLocal()
    try:
        try:
            result = session.execute(text("SELECT COUNT(*) FROM deals"))
            total_deals = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Could not count deals: {e}")
            total_deals = 0
        
        try:
            result = session.execute(text("SELECT COUNT(*) FROM users"))
            total_users = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Could not count users: {e}")
            total_users = 0
        
        # Count by plan
        plan_counts = {"freemium": 0, "basic": 0, "premium": 0}
        try:
            users = session.query(User).all()
            for u in users:
                plan = (u.plan or "free").lower()
                if plan in ("premium", "pro", "agency", "owner"):
                    plan_counts["premium"] += 1
                elif plan == "basic":
                    plan_counts["basic"] += 1
                else:
                    plan_counts["freemium"] += 1
        except:
            pass
        
        metrics = get_all_source_metrics()
        active_sources = sum(1 for m in metrics.values() if not m.is_blocked)
        
        last_scrape = None
        for m in metrics.values():
            if m.last_success_at:
                if last_scrape is None or m.last_success_at > last_scrape:
                    last_scrape = m.last_success_at
        
        # Count proxies
        proxy_count = session.query(ProxySettings).filter(ProxySettings.enabled == True).count()
        
        return {
            "database": "connected",
            "scraping": "idle",
            "last_scrape": last_scrape.isoformat() if last_scrape else None,
            "total_deals": total_deals,
            "total_users": total_users,
            "users_by_plan": plan_counts,
            "active_sources": active_sources,
            "active_proxies": proxy_count,
        }
    finally:
        session.close()


# =============================================================================
# USER MANAGEMENT
# =============================================================================

class UserResponse(BaseModel):
    id: int
    email: str
    plan: str
    tier: str
    is_admin: bool


class UserPlanUpdate(BaseModel):
    plan: str  # freemium, basic, premium


@router.get("/users", response_model=List[UserResponse])
def list_users(current_admin: dict = Depends(get_current_admin)):
    """List all users with their plans."""
    session = SessionLocal()
    try:
        users = session.query(User).all()
        result = []
        for u in users:
            plan = u.plan or "free"
            # Map to tier
            if plan.lower() in ("premium", "pro", "agency", "owner"):
                tier = "premium"
            elif plan.lower() == "basic":
                tier = "basic"
            else:
                tier = "freemium"
            
            result.append(UserResponse(
                id=u.id,
                email=u.email,
                plan=plan,
                tier=tier,
                is_admin=u.is_admin
            ))
        return result
    finally:
        session.close()


@router.patch("/users/{user_id}/plan")
def update_user_plan(user_id: int, payload: UserPlanUpdate, current_admin: dict = Depends(get_current_admin)):
    """Update a user plan (freemium, basic, premium)."""
    valid_plans = ["freemium", "free", "basic", "premium", "pro", "agency", "owner"]
    if payload.plan.lower() not in valid_plans:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Must be one of: {valid_plans}")
    
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        old_plan = user.plan
        user.plan = payload.plan.lower()
        session.commit()
        
        logger.info(f"User {user.email} plan changed: {old_plan} -> {user.plan}")
        
        return {
            "id": user.id,
            "email": user.email,
            "old_plan": old_plan,
            "new_plan": user.plan,
            "message": f"Plan updated to {user.plan}"
        }
    finally:
        session.close()


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_admin: dict = Depends(get_current_admin)):
    """Delete a user account."""
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if user.email == "admin@sharkted.fr":
            raise HTTPException(status_code=403, detail="Cannot delete admin account")
        
        email = user.email
        session.delete(user)
        session.commit()
        
        logger.info(f"User deleted: {email}")
        return {"message": f"User {email} deleted"}
    finally:
        session.close()


# =============================================================================
# SUBSCRIPTION TIERS INFO
# =============================================================================

@router.get("/subscription-tiers")
def get_subscription_tiers():
    """Get information about subscription tiers."""
    return {
        "tiers": [
            {
                "name": "freemium",
                "display_name": "Freemium",
                "price": 0,
                "limits": get_tier_limits(SubscriptionTier.FREEMIUM),
                "sources": list(BASIC_SOURCES),
                "description": "5 deals/jour gratuits. 1 top deal + deals moyens."
            },
            {
                "name": "basic",
                "display_name": "Basic",
                "price": 9.99,
                "limits": get_tier_limits(SubscriptionTier.BASIC),
                "sources": list(BASIC_SOURCES),
                "description": "Accès illimité aux sources basiques. Alertes et favoris."
            },
            {
                "name": "premium",
                "display_name": "Premium",
                "price": 29.99,
                "limits": get_tier_limits(SubscriptionTier.PREMIUM),
                "sources": list(BASIC_SOURCES | PREMIUM_SOURCES),
                "description": "Toutes les sources + Vinted scoring + exports."
            },
        ],
        "basic_sources": list(BASIC_SOURCES),
        "premium_sources": list(PREMIUM_SOURCES),
    }


# =============================================================================
# PROXY MANAGEMENT
# =============================================================================

class ProxyCreate(BaseModel):
    name: str
    provider: str = "brightdata"
    proxy_type: str = "web_unlocker"
    host: str
    port: int
    username: str
    password: str
    country: str = "FR"
    zone: Optional[str] = None
    enabled: bool = True
    is_default: bool = False


class ProxyUpdate(BaseModel):
    name: Optional[str] = None
    proxy_type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    country: Optional[str] = None
    zone: Optional[str] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


@router.get("/proxies")
def list_proxies():
    """List all configured proxies."""
    session = SessionLocal()
    try:
        proxies = session.query(ProxySettings).all()
        return {
            "proxies": [p.to_dict(hide_password=True) for p in proxies],
            "total": len(proxies),
        }
    finally:
        session.close()


@router.post("/proxies")
def create_proxy(proxy: ProxyCreate):
    """Add a new proxy configuration."""
    session = SessionLocal()
    try:
        # If setting as default, unset others
        if proxy.is_default:
            session.query(ProxySettings).filter(
                ProxySettings.proxy_type == proxy.proxy_type
            ).update({"is_default": False})
        
        new_proxy = ProxySettings(
            name=proxy.name,
            provider=proxy.provider,
            proxy_type=proxy.proxy_type,
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            password=proxy.password,
            country=proxy.country,
            zone=proxy.zone,
            enabled=proxy.enabled,
            is_default=proxy.is_default,
        )
        session.add(new_proxy)
        session.commit()
        session.refresh(new_proxy)
        
        logger.info(f"Proxy created: {new_proxy.name} ({new_proxy.proxy_type})")
        
        return new_proxy.to_dict(hide_password=True)
    finally:
        session.close()


@router.get("/proxies/{proxy_id}")
def get_proxy(proxy_id: int):
    """Get a specific proxy configuration."""
    session = SessionLocal()
    try:
        proxy = session.query(ProxySettings).filter(ProxySettings.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")
        return proxy.to_dict(hide_password=False)  # Show password for editing
    finally:
        session.close()


@router.patch("/proxies/{proxy_id}")
def update_proxy(proxy_id: int, update: ProxyUpdate):
    """Update a proxy configuration."""
    session = SessionLocal()
    try:
        proxy = session.query(ProxySettings).filter(ProxySettings.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")
        
        # Update fields
        update_data = update.model_dump(exclude_unset=True)
        
        # If setting as default, unset others
        if update_data.get("is_default"):
            session.query(ProxySettings).filter(
                ProxySettings.id != proxy_id,
                ProxySettings.proxy_type == proxy.proxy_type
            ).update({"is_default": False})
        
        for key, value in update_data.items():
            setattr(proxy, key, value)
        
        session.commit()
        session.refresh(proxy)
        
        logger.info(f"Proxy updated: {proxy.name}")
        
        return proxy.to_dict(hide_password=True)
    finally:
        session.close()


@router.delete("/proxies/{proxy_id}")
def delete_proxy(proxy_id: int):
    """Delete a proxy configuration."""
    session = SessionLocal()
    try:
        proxy = session.query(ProxySettings).filter(ProxySettings.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")
        
        name = proxy.name
        session.delete(proxy)
        session.commit()
        
        logger.info(f"Proxy deleted: {name}")
        return {"message": f"Proxy {name} deleted"}
    finally:
        session.close()


@router.post("/proxies/{proxy_id}/test")
async def test_proxy(proxy_id: int):
    """Test a proxy by making a request."""
    import httpx
    import time
    
    session = SessionLocal()
    try:
        proxy = session.query(ProxySettings).filter(ProxySettings.id == proxy_id).first()
        if not proxy:
            raise HTTPException(status_code=404, detail="Proxy not found")
        
        proxy_url = proxy.get_proxy_url()
        test_url = "https://geo.brdtest.com/mygeo.json" if "brightdata" in proxy.provider.lower() else "https://httpbin.org/ip"
        
        start = time.time()
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=30,
                verify=False,
            ) as client:
                resp = await client.get(test_url)
                duration_ms = (time.time() - start) * 1000
                
                # Update stats
                proxy.last_used_at = datetime.utcnow()
                proxy.success_count += 1
                session.commit()
                
                return {
                    "status": "success",
                    "proxy_name": proxy.name,
                    "status_code": resp.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:500],
                }
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            
            # Update error stats
            proxy.error_count += 1
            session.commit()
            
            return {
                "status": "error",
                "proxy_name": proxy.name,
                "error": str(e),
                "duration_ms": round(duration_ms, 2),
            }
    finally:
        session.close()


@router.get("/proxies/default/{proxy_type}")
def get_default_proxy(proxy_type: str):
    """Get the default proxy for a given type."""
    session = SessionLocal()
    try:
        proxy = session.query(ProxySettings).filter(
            ProxySettings.proxy_type == proxy_type,
            ProxySettings.enabled == True,
            ProxySettings.is_default == True
        ).first()
        
        if not proxy:
            # Fallback to any enabled proxy of this type
            proxy = session.query(ProxySettings).filter(
                ProxySettings.proxy_type == proxy_type,
                ProxySettings.enabled == True
            ).first()
        
        if not proxy:
            return {"proxy": None, "message": f"No {proxy_type} proxy configured"}
        
        return {
            "proxy": proxy.to_dict(hide_password=True),
            "proxy_url": proxy.get_proxy_url(),
        }
    finally:
        session.close()


# =============================================================================
# PROXY USAGE & COSTS
# =============================================================================

from app.services.proxy_decision_service import get_usage_stats, get_active_premium_count
from app.models.proxy_usage import ProxyUsage
from app.models.discord_webhook import DiscordWebhook


@router.get("/proxy-costs")
def get_proxy_costs(days: int = 7):
    """
    Get Web Unlocker usage statistics and costs.
    
    Shows:
    - Total requests and costs
    - Breakdown by trigger type (alert, high_score, fallback)
    - Breakdown by site
    - Daily costs
    - Cost efficiency (cost per Premium user)
    """
    stats = get_usage_stats(days=days)
    
    # Add business metrics
    premium_count = get_active_premium_count()
    if premium_count > 0 and stats["total_requests"] > 0:
        stats["cost_per_premium_user"] = round(
            stats["total_cost_eur"] / premium_count, 4
        )
        stats["avg_served_per_request"] = round(
            sum(1 for _ in range(stats["total_requests"])) / stats["total_requests"], 1
        )
    else:
        stats["cost_per_premium_user"] = 0
        stats["avg_served_per_request"] = 0
    
    # Add projected monthly cost
    if stats["total_requests"] > 0:
        daily_avg = stats["total_cost_eur"] / days
        stats["projected_monthly_cost"] = round(daily_avg * 30, 2)
    else:
        stats["projected_monthly_cost"] = 0
    
    return stats


@router.get("/proxy-costs/history")
def get_proxy_costs_history(
    page: int = 1,
    per_page: int = 50,
    current_admin: dict = Depends(get_current_admin)
):
    """Get detailed proxy usage history from database."""
    session = SessionLocal()
    try:
        query = session.query(ProxyUsage).order_by(ProxyUsage.created_at.desc())
        
        total = query.count()
        offset = (page - 1) * per_page
        logs = query.offset(offset).limit(per_page).all()
        
        return {
            "logs": [l.to_dict() for l in logs],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    finally:
        session.close()


@router.get("/proxy-decision-test")
def test_proxy_decision(
    site: str,
    score: float = 0,
    has_alert: bool = False,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Test the proxy decision logic for a given scenario.
    
    Useful for debugging and understanding when Web Unlocker is triggered.
    """
    from app.services.proxy_decision_service import should_use_web_unlocker, get_site_protection_level
    
    decision = should_use_web_unlocker(
        site=site,
        product_score=score,
        has_premium_alert=has_alert,
        is_fallback_after_403=False,
    )
    
    return {
        "site": site,
        "protection_level": get_site_protection_level(site).value,
        "input": {
            "score": score,
            "has_alert": has_alert,
        },
        "decision": decision.to_dict(),
        "premium_users_active": get_active_premium_count(),
    }


# =============================================================================
# AI SCORING TEST
# =============================================================================

@router.post("/test-ai-scoring")
async def test_ai_scoring(
    product_name: str,
    brand: str,
    original_price: float,
    sale_price: float,
):
    """
    Teste le scoring enrichi par IA pour un produit.
    Utile pour comprendre comment l'IA évalue un deal.
    """
    from app.services.scoring_service import score_deal_with_ai
    
    discount_pct = ((original_price - sale_price) / original_price * 100) if original_price > 0 else 0
    
    deal_data = {
        "title": product_name,
        "product_name": product_name,
        "brand": brand,
        "original_price": original_price,
        "sale_price": sale_price,
        "price": sale_price,
        "discount_percent": discount_pct,
    }
    
    # Score avec IA
    result_with_ai = await score_deal_with_ai(deal_data, use_ai=True)
    
    # Score sans IA (pour comparaison)
    from app.services.scoring_service import score_deal
    result_without_ai = await score_deal(deal_data, None)
    
    return {
        "product": {
            "name": product_name,
            "brand": brand,
            "original_price": original_price,
            "sale_price": sale_price,
            "discount_pct": round(discount_pct, 1),
        },
        "scoring_without_ai": {
            "flip_score": result_without_ai["flip_score"],
            "recommendation": result_without_ai["recommended_action"],
            "model": result_without_ai["model_version"],
        },
        "scoring_with_ai": {
            "flip_score": result_with_ai["flip_score"],
            "base_score": result_with_ai.get("base_score"),
            "ai_adjustment": result_with_ai.get("ai_adjustment"),
            "recommendation": result_with_ai["recommended_action"],
            "ai_method": result_with_ai.get("ai_method"),
            "ai_reasoning": result_with_ai.get("ai_reasoning"),
            "model": result_with_ai["model_version"],
        },
        "ai_analysis": result_with_ai.get("ai_analysis", {}),
    }


@router.get("/ai-cache-stats")
def get_ai_cache_stats():
    """Statistiques du cache IA."""
    from app.services.ai_scoring_enhancer import get_cache_stats
    return get_cache_stats()


# =============================================================================
# WEB UNLOCKER STATS - Suivi des coûts Premium
# =============================================================================

@router.get("/web-unlocker/stats")
def get_web_unlocker_stats(date: str = None):
    """
    Retourne les statistiques d'utilisation Web Unlocker.
    
    Inclut:
    - Nombre total de requêtes
    - Coût total estimé
    - Répartition par site
    - Répartition par trigger (alert, score, fallback)
    - Nombre d'utilisateurs Premium servis
    """
    try:
        from app.services.premium_gate import get_web_unlocker_stats as get_stats
        return get_stats(date)
    except Exception as e:
        return {"error": str(e), "date": date}


@router.get("/web-unlocker/requests")
def get_web_unlocker_requests(limit: int = 50):
    """
    Retourne les requêtes Web Unlocker récentes.
    
    Permet de voir en détail chaque requête:
    - URL, site, produit
    - Trigger (pourquoi la requête a été faite)
    - Coût
    - Users servis
    - Succès/échec
    """
    try:
        from app.services.premium_gate import premium_gate
        return {
            "requests": premium_gate.get_recent_requests(limit),
            "total_in_memory": len(premium_gate._request_log),
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/scraping/stats")
def get_scraping_stats():
    """
    Retourne les statistiques complètes de scraping.

    Inclut stats orchestrateur + Web Unlocker.
    """
    try:
        from app.services.scraping_orchestrator import get_premium_scraping_stats
        return get_premium_scraping_stats()
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# DISCORD WEBHOOKS - Par niveau d'abonnement
# =============================================================================

class DiscordWebhookUpdate(BaseModel):
    webhook_url: Optional[str] = None
    enabled: Optional[bool] = None
    send_after_scan: Optional[bool] = None
    min_score: Optional[int] = None
    send_daily_summary: Optional[bool] = None


class TierWebhooksUpdate(BaseModel):
    freemium: Optional[str] = None
    basic: Optional[str] = None
    premium: Optional[str] = None
    admin: Optional[str] = None


@router.get("/discord-webhooks")
def get_discord_webhooks(current_admin: dict = Depends(get_current_admin)):
    """Get all Discord webhook configurations by tier."""
    session = SessionLocal()
    try:
        webhooks = session.query(DiscordWebhook).all()

        # Return as dict keyed by tier
        result = {
            "freemium": None,
            "basic": None,
            "premium": None,
            "admin": None,
        }

        for wh in webhooks:
            result[wh.tier] = wh.to_dict()

        return result
    finally:
        session.close()


@router.put("/discord-webhooks")
def update_all_discord_webhooks(
    payload: TierWebhooksUpdate,
    current_admin: dict = Depends(get_current_admin)
):
    """Update Discord webhooks for all tiers at once."""
    session = SessionLocal()
    try:
        tiers = ["freemium", "basic", "premium", "admin"]
        results = {}

        for tier in tiers:
            webhook_url = getattr(payload, tier)

            # Find or create webhook for this tier
            webhook = session.query(DiscordWebhook).filter(
                DiscordWebhook.tier == tier
            ).first()

            if webhook:
                # Update existing
                if webhook_url is not None:
                    webhook.webhook_url = webhook_url if webhook_url else None
            else:
                # Create new
                webhook = DiscordWebhook(
                    tier=tier,
                    webhook_url=webhook_url if webhook_url else None,
                    enabled=True,
                    send_after_scan=True,
                    min_score=70,
                    send_daily_summary=False,
                )
                session.add(webhook)

            results[tier] = {
                "webhook_url": webhook_url,
                "updated": True,
            }

        session.commit()

        logger.info(f"Discord webhooks updated by {current_admin['email']}")

        return {
            "message": "Webhooks updated successfully",
            "results": results,
        }
    finally:
        session.close()


@router.put("/discord-webhooks/{tier}")
def update_discord_webhook(
    tier: str,
    payload: DiscordWebhookUpdate,
    current_admin: dict = Depends(get_current_admin)
):
    """Update Discord webhook for a specific tier."""
    valid_tiers = ["freemium", "basic", "premium", "admin"]
    if tier not in valid_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier. Must be one of: {valid_tiers}"
        )

    session = SessionLocal()
    try:
        webhook = session.query(DiscordWebhook).filter(
            DiscordWebhook.tier == tier
        ).first()

        if not webhook:
            # Create new
            webhook = DiscordWebhook(tier=tier)
            session.add(webhook)

        # Update fields
        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(webhook, key, value)

        session.commit()
        session.refresh(webhook)

        logger.info(f"Discord webhook for {tier} updated by {current_admin['email']}")

        return webhook.to_dict()
    finally:
        session.close()


@router.post("/discord-webhooks/{tier}/test")
async def test_discord_webhook(
    tier: str,
    current_admin: dict = Depends(get_current_admin)
):
    """Test a Discord webhook by sending a test message."""
    import httpx

    valid_tiers = ["freemium", "basic", "premium", "admin"]
    if tier not in valid_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier. Must be one of: {valid_tiers}"
        )

    session = SessionLocal()
    try:
        webhook = session.query(DiscordWebhook).filter(
            DiscordWebhook.tier == tier
        ).first()

        if not webhook or not webhook.webhook_url:
            raise HTTPException(
                status_code=404,
                detail=f"No webhook configured for tier: {tier}"
            )

        # Send test message
        test_payload = {
            "embeds": [{
                "title": "Test Sharkted Alert",
                "description": f"This is a test message for the **{tier.upper()}** tier webhook.",
                "color": 0x00ff00,  # Green
                "fields": [
                    {"name": "Tier", "value": tier.upper(), "inline": True},
                    {"name": "Status", "value": "Working", "inline": True},
                ],
                "footer": {"text": "Sharkted Alert System"}
            }]
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                webhook.webhook_url,
                json=test_payload
            )

            if response.status_code == 204:
                return {
                    "status": "success",
                    "tier": tier,
                    "message": "Test message sent successfully"
                }
            else:
                return {
                    "status": "error",
                    "tier": tier,
                    "status_code": response.status_code,
                    "error": response.text
                }
    except httpx.RequestError as e:
        return {
            "status": "error",
            "tier": tier,
            "error": str(e)
        }
    finally:
        session.close()


@router.get("/discord-webhooks/{tier}")
def get_discord_webhook(tier: str, current_admin: dict = Depends(get_current_admin)):
    """Get Discord webhook configuration for a specific tier."""
    valid_tiers = ["freemium", "basic", "premium", "admin"]
    if tier not in valid_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier. Must be one of: {valid_tiers}"
        )

    session = SessionLocal()
    try:
        webhook = session.query(DiscordWebhook).filter(
            DiscordWebhook.tier == tier
        ).first()

        if not webhook:
            return {
                "tier": tier,
                "webhook_url": None,
                "enabled": False,
                "message": "No webhook configured for this tier"
            }

        return webhook.to_dict()
    finally:
        session.close()
