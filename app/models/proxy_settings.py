"""
Proxy Settings model - Store proxy configurations in database.
"""
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, Text, JSON
from datetime import datetime
from typing import Optional

from app.models.user import Base


class ProxySettings(Base):
    """Store proxy configurations that can be managed via admin UI."""
    __tablename__ = "proxy_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "BrightData Web Unlocker"
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., "brightdata", "oxylabs"
    proxy_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "residential", "web_unlocker", "datacenter"
    
    # Connection details
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Options
    country: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="FR")
    zone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # BrightData zone name
    extra_params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # Additional params
    
    # Status
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Usage tracking
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def get_proxy_url(self) -> str:
        """Generate the proxy URL string."""
        auth = f"{self.username}:{self.password}"
        return f"http://{auth}@{self.host}:{self.port}"
    
    def get_curl_proxy_args(self) -> tuple:
        """Return (proxy_host:port, username:password) for curl."""
        return (f"{self.host}:{self.port}", f"{self.username}:{self.password}")

    def to_dict(self, hide_password: bool = True) -> dict:
        """Convert to dictionary for API response."""
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "proxy_type": self.proxy_type,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": "***" if hide_password else self.password,
            "country": self.country,
            "zone": self.zone,
            "extra_params": self.extra_params,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate": round(self.success_count / max(self.success_count + self.error_count, 1) * 100, 1),
            "created_at": self.created_at.isoformat(),
        }
