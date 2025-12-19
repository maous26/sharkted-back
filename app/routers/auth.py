from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import os

from app.db.deps import get_db
from app.models.user import User
from app.core.security import hash_password, verify_password, create_access_token
from app.core.rate_limiter import rate_limit_login, rate_limit_register
from app.core.subscription_tiers import (
    get_tier_from_plan,
    get_tier_limits,
    get_allowed_sources,
    get_tier_info,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Cookie configuration
COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
IS_PRODUCTION = os.getenv("ENV", "development") == "production"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Register a new user."""
    rate_limit_register(request)
    email = payload.email.strip().lower()

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    return {"status": "created"}


@router.post("/login")
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    use_cookie: bool = True,
):
    """Login and get access token."""
    rate_limit_login(request)
    email = payload.email.strip().lower()

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(subject=user.email)

    if use_cookie:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=IS_PRODUCTION,
            samesite="lax",
        )

    plan = user.plan or "free"
    tier = get_tier_from_plan(plan)
    limits = get_tier_limits(tier)
    is_admin = user.email == "admin@sharkted.fr" or plan == "owner"

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "plan": plan.upper(),
            "tier": tier.value,
            "is_admin": is_admin,
            "limits": {
                "max_deals": limits.max_deals,
                "vinted_scoring": limits.vinted_scoring,
                "premium_sources": limits.premium_sources,
                "alerts_enabled": limits.alerts_enabled,
                "favorites_enabled": limits.favorites_enabled,
                "export_enabled": limits.export_enabled,
            },
        }
    }


@router.post("/logout")
def logout(response: Response):
    """Logout - clear the auth cookie."""
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="lax",
    )
    return {"status": "logged_out"}


def get_current_user_from_request(request: Request, db: Session) -> User:
    """Extract and validate current user from token."""
    from jose import jwt, JWTError
    from app.core.config import JWT_SECRET, JWT_ALGO

    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


@router.get("/me")
def get_me(request: Request, db: Session = Depends(get_db)):
    """Get current authenticated user info with tier details."""
    user = get_current_user_from_request(request, db)

    plan = user.plan or "free"
    tier = get_tier_from_plan(plan)
    limits = get_tier_limits(tier)
    is_admin = user.email == "admin@sharkted.fr" or plan == "owner"

    return {
        "id": user.id,
        "email": user.email,
        "plan": plan.upper() if plan else "FREE",
        "tier": tier.value,
        "is_admin": is_admin,
        "limits": {
            "max_deals": limits.max_deals,
            "vinted_scoring": limits.vinted_scoring,
            "premium_sources": limits.premium_sources,
            "alerts_enabled": limits.alerts_enabled,
            "favorites_enabled": limits.favorites_enabled,
            "export_enabled": limits.export_enabled,
        },
        "allowed_sources": list(get_allowed_sources(tier)),
    }


# =============================================================================
# ADMIN ENDPOINTS - User Management (owner only)
# =============================================================================

VALID_PLANS = ["free", "basic", "premium", "pro", "agency", "owner"]


@router.get("/admin/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    """List all users (owner only)."""
    current_user = get_current_user_from_request(request, db)

    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "plan": (u.plan or "free").upper(),
            "tier": get_tier_from_plan(u.plan).value,
            "is_owner": u.email == "admin@sharkted.fr" or u.plan == "owner",
        }
        for u in users
    ]


class UpdateUserPlan(BaseModel):
    plan: str


@router.patch("/admin/users/{user_id}")
def update_user_plan(
    user_id: int,
    payload: UpdateUserPlan,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update a user's plan (owner only)."""
    current_user = get_current_user_from_request(request, db)

    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    plan = payload.plan.lower()
    if plan not in VALID_PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan. Must be one of: {', '.join(VALID_PLANS)}"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.email == "admin@sharkted.fr":
        raise HTTPException(status_code=400, detail="Cannot modify owner account")

    user.plan = plan
    db.commit()

    tier = get_tier_from_plan(plan)
    return {
        "id": user.id,
        "email": user.email,
        "plan": plan.upper(),
        "tier": tier.value,
        "message": f"Plan updated to {plan.upper()} (tier: {tier.value})",
    }


@router.delete("/admin/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a user (owner only)."""
    current_user = get_current_user_from_request(request, db)

    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.email == "admin@sharkted.fr":
        raise HTTPException(status_code=400, detail="Cannot delete owner account")

    db.delete(user)
    db.commit()

    return {"message": f"User {user.email} deleted"}


# =============================================================================
# USER PREFERENCES
# =============================================================================

PRODUCT_CATEGORIES = [
    "sneakers", "sacs", "doudounes", "vestes", "t-shirts", "sweats",
    "pantalons", "robes", "accessoires", "montres", "lunettes", "chaussures",
]


class UpdatePreferences(BaseModel):
    categories: Optional[List[str]] = None
    other_categories: Optional[str] = None


@router.get("/me/preferences")
def get_preferences(request: Request, db: Session = Depends(get_db)):
    """Get current user's category preferences."""
    user = get_current_user_from_request(request, db)
    preferences = user.preferences or {}

    return {
        "categories": preferences.get("categories", []),
        "other_categories": preferences.get("other_categories", ""),
        "available_categories": PRODUCT_CATEGORIES,
    }


@router.patch("/me/preferences")
def update_preferences(
    payload: UpdatePreferences,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update current user's category preferences."""
    user = get_current_user_from_request(request, db)
    preferences = user.preferences or {}

    if payload.categories is not None:
        invalid = [c for c in payload.categories if c not in PRODUCT_CATEGORIES and c != "autre"]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid categories: {', '.join(invalid)}"
            )
        preferences["categories"] = payload.categories

    if payload.other_categories is not None:
        preferences["other_categories"] = payload.other_categories.strip()

    user.preferences = preferences
    db.commit()

    return {
        "categories": preferences.get("categories", []),
        "other_categories": preferences.get("other_categories", ""),
        "message": "Preferences updated successfully",
    }


@router.get("/categories")
def get_available_categories():
    """Get list of available product categories."""
    return {"categories": PRODUCT_CATEGORIES}


@router.get("/subscription/tiers")
def get_subscription_tiers():
    """Get all subscription tiers and their features."""
    from app.core.subscription_tiers import SubscriptionTier, TIER_LIMITS, FREE_SOURCES, PREMIUM_SOURCES

    tiers = []
    for tier in SubscriptionTier:
        limits = TIER_LIMITS[tier]
        tiers.append({
            "tier": tier.value,
            "limits": {
                "max_deals": limits.max_deals,
                "max_top_deals": limits.max_top_deals,
                "vinted_scoring": limits.vinted_scoring,
                "premium_sources": limits.premium_sources,
                "alerts_enabled": limits.alerts_enabled,
                "favorites_enabled": limits.favorites_enabled,
                "export_enabled": limits.export_enabled,
            },
        })

    return {
        "tiers": tiers,
        "free_sources": list(FREE_SOURCES),
        "premium_sources": list(PREMIUM_SOURCES),
    }


# =============================================================================
# DISCORD OAUTH - Liaison de compte
# =============================================================================

@router.get("/discord/link")
def get_discord_link_url(request: Request, db: Session = Depends(get_db)):
    """
    Get Discord OAuth URL to link account.
    The state contains the user ID for verification.
    """
    from app.services.discord_service import get_oauth_url
    import secrets

    user = get_current_user_from_request(request, db)

    # Generate state with user ID
    state = f"{user.id}:{secrets.token_urlsafe(16)}"

    return {
        "oauth_url": get_oauth_url(state),
        "state": state,
    }


class DiscordCallbackIn(BaseModel):
    code: str
    state: str


@router.post("/discord/callback")
async def discord_callback(
    payload: DiscordCallbackIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Discord OAuth callback.
    Links the Discord account to the current user.
    """
    from app.services.discord_service import link_discord_account

    user = get_current_user_from_request(request, db)

    # Verify state contains correct user ID
    try:
        state_user_id = int(payload.state.split(":")[0])
        if state_user_id != user.id:
            raise HTTPException(status_code=400, detail="Invalid state")
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid state format")

    result = await link_discord_account(user.id, payload.code)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to link Discord"))

    return {
        "success": True,
        "discord_id": result.get("discord_id"),
        "discord_username": result.get("discord_username"),
        "tier": result.get("tier"),
        "message": "Discord account linked successfully",
    }


@router.delete("/discord/unlink")
async def unlink_discord(request: Request, db: Session = Depends(get_db)):
    """Unlink Discord account from current user."""
    from app.services.discord_service import unlink_discord_account

    user = get_current_user_from_request(request, db)

    if not user.discord_id:
        raise HTTPException(status_code=400, detail="No Discord account linked")

    success = await unlink_discord_account(user.id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to unlink Discord")

    return {"success": True, "message": "Discord account unlinked"}


@router.get("/discord/status")
def get_discord_status(request: Request, db: Session = Depends(get_db)):
    """Get Discord link status for current user."""
    user = get_current_user_from_request(request, db)

    return {
        "linked": user.discord_id is not None,
        "discord_id": user.discord_id,
        "discord_username": user.discord_username,
    }
