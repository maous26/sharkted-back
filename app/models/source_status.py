"""
Modèle SQLAlchemy pour l'état des sources.

Persistance optionnelle des métriques sources.
Le tracker en mémoire reste la source de vérité pour les décisions temps réel,
mais on persiste périodiquement pour l'historique et les dashboards.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SourceStatus(Base):
    """État persisté d'une source de collecte."""

    __tablename__ = "source_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)

    # Mode actuel
    current_mode: Mapped[str] = mapped_column(String(20), default="direct")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Compteurs
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_success: Mapped[int] = mapped_column(Integer, default=0)
    total_failures: Mapped[int] = mapped_column(Integer, default=0)

    # Stats 24h
    success_24h: Mapped[int] = mapped_column(Integer, default=0)
    failures_24h: Mapped[int] = mapped_column(Integer, default=0)

    # Dernières activités
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Blocage
    blocked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    block_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    @property
    def success_rate_24h(self) -> float:
        total = self.success_24h + self.failures_24h
        if total == 0:
            return 0.0
        return round(self.success_24h / total * 100, 1)

    @property
    def is_blocked(self) -> bool:
        if self.blocked_until is None:
            return False
        return datetime.utcnow() < self.blocked_until

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "current_mode": self.current_mode,
            "enabled": self.enabled,
            "total_attempts": self.total_attempts,
            "total_success": self.total_success,
            "total_failures": self.total_failures,
            "success_rate_24h": self.success_rate_24h,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "last_error_type": self.last_error_type,
            "last_status_code": self.last_status_code,
            "is_blocked": self.is_blocked,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
            "block_reason": self.block_reason,
        }
