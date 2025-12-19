"""VintedStats model - Statistiques Vinted pour un deal."""
from datetime import datetime
from typing import Optional, List, Any
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class VintedStats(Base):
    """Statistiques de marché Vinted pour un deal spécifique."""
    
    __tablename__ = "vinted_stats"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), nullable=False, unique=True)
    
    # Nombre d'annonces
    nb_listings: Mapped[int] = mapped_column(Integer, default=0)
    
    # Statistiques de prix (en euros)
    price_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_median: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_p25: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_p75: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coefficient_variation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Métriques calculées
    margin_euro: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Exemples d'annonces
    sample_listings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    # Requête utilisée
    search_query: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Source et condition des données
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "vinted_real" = données réelles
    condition: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "new_with_tags" = neuf avec étiquette

    # Timestamps
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    deal = relationship("Deal", back_populates="vinted_stats")
    
    def __repr__(self) -> str:
        return f"<VintedStats deal={self.deal_id} margin={self.margin_pct}%>"
