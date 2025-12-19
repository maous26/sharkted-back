"""
Discord Webhook Settings - Webhooks par niveau d'abonnement.

Chaque tier peut avoir son propre webhook Discord pour recevoir les alertes.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.db.session import Base


class DiscordWebhook(Base):
    """Discord webhook configuration per subscription tier."""
    __tablename__ = "discord_webhooks"

    id = Column(Integer, primary_key=True)
    tier = Column(String(50), unique=True, nullable=False)  # freemium, basic, premium, admin
    webhook_url = Column(String(500), nullable=True)
    enabled = Column(Boolean, default=True)
    send_after_scan = Column(Boolean, default=True)
    min_score = Column(Integer, default=70)  # Score minimum pour alertes
    send_daily_summary = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "tier": self.tier,
            "webhook_url": self.webhook_url,
            "enabled": self.enabled,
            "send_after_scan": self.send_after_scan,
            "min_score": self.min_score,
            "send_daily_summary": self.send_daily_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
