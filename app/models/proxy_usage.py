"""
Proxy Usage model - Track Web Unlocker requests and costs.
"""
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text
from datetime import datetime
from typing import Optional

from app.models.user import Base


class ProxyUsage(Base):
    """Track every Web Unlocker request for cost analysis."""
    __tablename__ = "proxy_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    
    # Request info
    site: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Decision info
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)  # alert, high_score, fallback_403
    decision_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Cost tracking
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    served_users: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cost_per_user: Mapped[float] = mapped_column(Float, nullable=False, default=0)  # cost / served_users
    
    # Result
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    response_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "site": self.site,
            "url": self.url[:100] + "..." if len(self.url) > 100 else self.url,
            "trigger_type": self.trigger_type,
            "cost_estimate": self.cost_estimate,
            "served_users": self.served_users,
            "cost_per_user": self.cost_per_user,
            "success": self.success,
            "response_code": self.response_code,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at.isoformat(),
        }
