"""DealScore model - Scoring IA pour les deals."""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import ARRAY

from app.models.user import Base


class DealScore(Base):
    """Scoring et recommandations IA pour un deal."""
    
    __tablename__ = "deal_scores"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False, unique=True)
    
    # Scores principaux (0-100)
    flip_score: Mapped[float] = mapped_column(Float, nullable=False)
    popularity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Détail des composantes du score
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Recommandations
    recommended_action: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # 'buy', 'watch', 'ignore'
    recommended_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Explication LLM
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_short: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    risks: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # Liste des risques
    
    # Prédictions
    estimated_sell_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Métadonnées du modèle
    model_version: Mapped[str] = mapped_column(String(50), default="rules_v1")
    
    # Timestamps
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    deal = relationship("Deal", back_populates="score_data")
    
    def __repr__(self) -> str:
        return f"<DealScore {self.flip_score}/100 - {self.recommended_action}>"
    
    @property
    def score_emoji(self) -> str:
        """Get emoji based on flip score."""
        if self.flip_score >= 80:
            return "🟢"
        elif self.flip_score >= 60:
            return "🟡"
        elif self.flip_score >= 40:
            return "🟠"
        else:
            return "🔴"
    
    @property
    def is_recommended(self) -> bool:
        """Check if deal is recommended for purchase."""
        return self.recommended_action == "buy" and self.flip_score >= 70
