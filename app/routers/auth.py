from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List, Optional
import os

from app.db.deps import get_db
from app.models.user import User
from app.core.security import hash_password, verify_password, create_access_token
from app.core.rate_limiter import rate_limit_login, rate_limit_register

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
    # Rate limit: 3/min per IP
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
    """
    Login and get access token.

    Returns token in both:
    - Response body (for API clients using Bearer auth)
    - HttpOnly cookie (for web browsers - more secure)

    Set use_cookie=false to skip cookie (API clients only).
    """
    # Rate limit: 5/min per IP (brute force protection)
    rate_limit_login(request)

    email = payload.email.strip().lower()

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(subject=user.email)

    # Set HttpOnly cookie for web browsers
    if use_cookie:
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,  # Not accessible via JavaScript (XSS protection)
            secure=IS_PRODUCTION,  # HTTPS only in production
            samesite="lax",  # CSRF protection
        )

    # Build user info for frontend
    plan = user.plan or "free"
    is_admin = user.email == "admin@sharkted.fr" or plan == "owner"

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "plan": plan.upper(),
            "is_admin": is_admin,
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

    # Try cookie first, then Authorization header
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
    """Get current authenticated user info."""
    user = get_current_user_from_request(request, db)

    # Determine effective plan (owner has admin access)
    plan = user.plan or "free"
    is_admin = user.email == "admin@sharkted.fr" or plan == "owner"

    return {
        "id": user.id,
        "email": user.email,
        "plan": plan.upper() if plan else "FREE",
        "is_admin": is_admin,
    }


# =============================================================================
# ADMIN ENDPOINTS - User Management (owner only)
# =============================================================================

VALID_PLANS = ["free", "pro", "agency", "owner"]


@router.get("/admin/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    """List all users (owner only)."""
    current_user = get_current_user_from_request(request, db)

    # Only owner can access
    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    users = db.query(User).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "plan": (u.plan or "free").upper(),
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

    # Only owner can access
    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Validate plan
    plan = payload.plan.lower()
    if plan not in VALID_PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan. Must be one of: {', '.join(VALID_PLANS)}"
        )

    # Find user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot change owner's own plan
    if user.email == "admin@sharkted.fr":
        raise HTTPException(status_code=400, detail="Cannot modify owner account")

    user.plan = plan
    db.commit()

    return {
        "id": user.id,
        "email": user.email,
        "plan": plan.upper(),
        "message": f"Plan updated to {plan.upper()}",
    }


@router.delete("/admin/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a user (owner only)."""
    current_user = get_current_user_from_request(request, db)

    # Only owner can access
    if current_user.email != "admin@sharkted.fr" and current_user.plan != "owner":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Find user
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Cannot delete owner
    if user.email == "admin@sharkted.fr":
        raise HTTPException(status_code=400, detail="Cannot delete owner account")

    db.delete(user)
    db.commit()

    return {"message": f"User {user.email} deleted"}


# =============================================================================
# USER PREFERENCES - Category preferences for scraping/alerts
# =============================================================================

# Available product categories
PRODUCT_CATEGORIES = [
    "sneakers",
    "sacs",
    "doudounes",
    "vestes",
    "t-shirts",
    "sweats",
    "pantalons",
    "robes",
    "accessoires",
    "montres",
    "lunettes",
    "chaussures",
]


class UpdatePreferences(BaseModel):
    categories: Optional[List[str]] = None
    other_categories: Optional[str] = None  # Free text for custom categories


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

    # Get existing preferences or create new dict
    preferences = user.preferences or {}

    # Update categories if provided
    if payload.categories is not None:
        # Validate categories
        invalid = [c for c in payload.categories if c not in PRODUCT_CATEGORIES and c != "autre"]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid categories: {', '.join(invalid)}. Valid: {', '.join(PRODUCT_CATEGORIES)}"
            )
        preferences["categories"] = payload.categories

    # Update other_categories text if provided
    if payload.other_categories is not None:
        preferences["other_categories"] = payload.other_categories.strip()

    # Save to database
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
    return {
        "categories": PRODUCT_CATEGORIES,
    }

