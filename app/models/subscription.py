"""
Subscription model - Defines subscription tiers and their features.
"""
from enum import Enum
from typing import Dict, List, Set

class SubscriptionTier(str, Enum):
    """Subscription tiers."""
    FREEMIUM = "freemium"    # 5 deals/day free, mixed quality
    BASIC = "basic"          # All basic source deals  
    PREMIUM = "premium"      # All sources including premium (Vinted, Zalando, etc.)


# Sources configuration by tier
BASIC_SOURCES = {
    "courir",
    "asphaltgold", 
    "solebox",
    "kith",
    "footlocker",
    "jdsports",
    "size",
}

PREMIUM_SOURCES = {
    "vinted",
    "zalando",
    "printemps",
    "galerieslafayette",
    "laredoute",
    "sns",  # Sneakersnstuff
    "bstn",
}

ALL_SOURCES = BASIC_SOURCES | PREMIUM_SOURCES


def get_tier_sources(tier: SubscriptionTier) -> Set[str]:
    """Get allowed sources for a subscription tier."""
    if tier == SubscriptionTier.FREEMIUM:
        return BASIC_SOURCES
    elif tier == SubscriptionTier.BASIC:
        return BASIC_SOURCES
    elif tier == SubscriptionTier.PREMIUM:
        return ALL_SOURCES
    return BASIC_SOURCES


def get_tier_limits(tier: SubscriptionTier) -> Dict:
    """Get limits for a subscription tier."""
    if tier == SubscriptionTier.FREEMIUM:
        return {
            "daily_deals": 5,
            "show_top_deals": True,      # Show 1 top deal
            "show_medium_deals": True,   # Show some medium deals
            "hide_prices": False,        # Don't hide prices
            "alerts_enabled": False,
            "favorites_enabled": False,
            "export_enabled": False,
            "vinted_scoring": False,     # No Vinted-based scoring
        }
    elif tier == SubscriptionTier.BASIC:
        return {
            "daily_deals": -1,  # Unlimited
            "show_top_deals": True,
            "show_medium_deals": True,
            "hide_prices": False,
            "alerts_enabled": True,
            "favorites_enabled": True,
            "export_enabled": False,
            "vinted_scoring": False,
        }
    elif tier == SubscriptionTier.PREMIUM:
        return {
            "daily_deals": -1,  # Unlimited
            "show_top_deals": True,
            "show_medium_deals": True,
            "hide_prices": False,
            "alerts_enabled": True,
            "favorites_enabled": True,
            "export_enabled": True,
            "vinted_scoring": True,  # Real Vinted market data
        }
    return get_tier_limits(SubscriptionTier.FREEMIUM)


def get_user_tier(plan: str) -> SubscriptionTier:
    """Convert user plan string to SubscriptionTier."""
    plan_lower = (plan or "free").lower()
    if plan_lower in ("premium", "pro", "agency", "owner"):
        return SubscriptionTier.PREMIUM
    elif plan_lower == "basic":
        return SubscriptionTier.BASIC
    return SubscriptionTier.FREEMIUM
