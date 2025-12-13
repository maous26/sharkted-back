from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
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

    return {"access_token": token, "token_type": "bearer"}


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

