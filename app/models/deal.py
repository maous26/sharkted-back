from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, Float, Text, DateTime, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column

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

    # Disponibilité
    in_stock: Mapped[bool] = mapped_column(default=True)

    # Scoring (calculé plus tard)
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

    # Contrainte d'unicité sur (source, external_id)
    __table_args__ = (
        Index("ix_deals_source_external_id", "source", "external_id", unique=True),
        Index("ix_deals_price", "price"),
        Index("ix_deals_last_seen", "last_seen_at"),
    )

    def __repr__(self) -> str:
        return f"<Deal {self.source}:{self.external_id} - {self.title[:30]}... @ {self.price}{self.currency}>"
