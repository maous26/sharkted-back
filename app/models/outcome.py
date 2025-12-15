"""
Model Outcome - Tracking des achats et ventes utilisateurs
Pour collecter des données réelles et améliorer les prédictions ML
"""

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional

from app.models.user import Base


class Outcome(Base):
    __tablename__ = "outcomes"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    deal_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("deals.id"), nullable=True, index=True)
    
    # Action: bought, skipped, watching
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    
    # Détails achat
    buy_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    buy_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    buy_size: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    buy_platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Détails vente (si vendu)
    sold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sell_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sell_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sell_platform: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Métriques calculées
    actual_margin_euro: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_margin_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    days_to_sell: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Feedback utilisateur
    was_good_deal: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    difficulty_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Contexte au moment de l'achat (pour ML)
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
