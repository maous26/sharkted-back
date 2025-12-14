from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, Float, Text, DateTime, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class Deal(Base):
    """
    Modèle persistant pour un deal/produit collecté.

    Clé logique d'unicité: (source, external_id)
    - source: identifiant du site (adidas, laredoute, yoox, etc.)
    - external_id: SKU ou identifiant produit sur le site source
    """
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identification unique du produit
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Données produit
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Métadonnées vendeur
    seller_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Historique prix (pour tracking futur)
    original_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    discount_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Tailles disponibles
    sizes_available: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Disponibilité
    in_stock: Mapped[bool] = mapped_column(default=True)

    # Scoring (calculé par le service scoring)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Données brutes pour debug/reprocessing
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Timestamps
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    price_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    vinted_stats = relationship("VintedStats", back_populates="deal", uselist=False, lazy="joined")
    score_data = relationship("DealScore", back_populates="deal", uselist=False, lazy="joined")

    # Contrainte d'unicité sur (source, external_id)
    __table_args__ = (
        Index("ix_deals_source_external_id", "source", "external_id", unique=True),
        Index("ix_deals_price", "price"),
        Index("ix_deals_last_seen", "last_seen_at"),
        Index("ix_deals_brand", "brand"),
        Index("ix_deals_score", "score"),
    )

    def __repr__(self) -> str:
        return f"<Deal {self.source}:{self.external_id} - {self.title[:30]}... @ {self.price}{self.currency}>"
    
    def to_api_dict(self) -> dict:
        """Convertit le deal en dict pour l'API avec stats Vinted et score."""
        result = {
            "id": str(self.id),
            "product_name": self.title,
            "brand": self.brand or self.seller_name,
            "model": self.model,
            "category": self.category,
            "color": self.color,
            "gender": self.gender,
            "original_price": self.original_price,
            "sale_price": self.price,
            "discount_pct": self.discount_percent,
            "product_url": self.url,
            "image_url": self.image_url,
            "sizes_available": self.sizes_available,
            "stock_available": self.in_stock,
            "source_name": self.source,
            "detected_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
        }
        
        # Ajouter les stats Vinted si disponibles
        if self.vinted_stats:
            result["vinted_stats"] = {
                "nb_listings": self.vinted_stats.nb_listings,
                "price_min": self.vinted_stats.price_min,
                "price_max": self.vinted_stats.price_max,
                "price_median": self.vinted_stats.price_median,
                "margin_euro": self.vinted_stats.margin_euro,
                "margin_pct": self.vinted_stats.margin_pct,
                "liquidity_score": self.vinted_stats.liquidity_score,
            }
        else:
            result["vinted_stats"] = None
        
        # Ajouter le score si disponible
        if self.score_data:
            result["score"] = {
                "flip_score": self.score_data.flip_score,
                "recommended_action": self.score_data.recommended_action,
                "recommended_price": self.score_data.recommended_price,
                "confidence": self.score_data.confidence,
                "explanation_short": self.score_data.explanation_short,
                "risks": self.score_data.risks,
                "estimated_sell_days": self.score_data.estimated_sell_days,
            }
        else:
            result["score"] = None
        
        return result
