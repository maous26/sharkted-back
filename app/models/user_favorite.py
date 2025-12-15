"""UserFavorite model - Deals favoris des utilisateurs."""
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint, Text

from app.models.user import Base


class UserFavorite(Base):
    """Deal favori d'un utilisateur."""

    __tablename__ = "user_favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False)
    
    # Optionnel: notes personnelles
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Contrainte unique: un user ne peut pas avoir le même deal en favori 2 fois
    __table_args__ = (
        UniqueConstraint("user_id", "deal_id", name="uq_user_deal_favorite"),
    )

    def __repr__(self):
        return f"<UserFavorite user={self.user_id} deal={self.deal_id}>"
