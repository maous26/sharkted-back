"""
Subscription Tiers - Définition des niveaux d'abonnement et leurs limitations.

FREEMIUM (free, anonymous):
├── 5 deals max (1 top + 4 autres)
├── Sources: sans proxies, unlocker ou avec proxies rotatifs maisons
└── Pas de Vinted scoring

BASIC (basic):
├── Deals illimités
├── Sources: mêmes que freemium  
├── Alertes et favoris
└── Vinted scoring

PREMIUM (premium, pro, agency, owner):
├── Deals illimités
├── Toutes les sources (+ sources protégées: asos, printemps, etc.)
├── Alertes, favoris, export
└── Vinted scoring
"""
from enum import Enum
from typing import Set, List, Optional
from dataclasses import dataclass


class SubscriptionTier(str, Enum):
    """Niveaux d'abonnement."""
    FREEMIUM = "freemium"
    BASIC = "basic"
    PREMIUM = "premium"


# Mapping des plans DB vers les tiers
PLAN_TO_TIER = {
    # Freemium
    "free": SubscriptionTier.FREEMIUM,
    None: SubscriptionTier.FREEMIUM,
    "": SubscriptionTier.FREEMIUM,
    
    # Basic
    "basic": SubscriptionTier.BASIC,
    
    # Premium (tous les plans payants avancés)
    "premium": SubscriptionTier.PREMIUM,
    "pro": SubscriptionTier.PREMIUM,
    "agency": SubscriptionTier.PREMIUM,
    "owner": SubscriptionTier.PREMIUM,
}


@dataclass
class TierLimits:
    """Limitations par tier."""
    max_deals: Optional[int]  # None = illimité
    max_top_deals: int  # Nombre de deals "top" visibles
    vinted_scoring: bool  # Accès au scoring Vinted
    premium_sources: bool  # Accès aux sources protégées (web unlocker)
    alerts_enabled: bool  # Alertes personnalisées
    favorites_enabled: bool  # Favoris
    export_enabled: bool  # Export CSV/JSON


# Configuration des limites par tier
TIER_LIMITS = {
    SubscriptionTier.FREEMIUM: TierLimits(
        max_deals=5,  # 1 top + 4 autres
        max_top_deals=1,
        vinted_scoring=False,
        premium_sources=False,
        alerts_enabled=False,
        favorites_enabled=False,
        export_enabled=False,
    ),
    SubscriptionTier.BASIC: TierLimits(
        max_deals=None,  # Illimité
        max_top_deals=None,
        vinted_scoring=True,
        premium_sources=False,  # Même sources que freemium
        alerts_enabled=True,
        favorites_enabled=True,
        export_enabled=False,
    ),
    SubscriptionTier.PREMIUM: TierLimits(
        max_deals=None,  # Illimité
        max_top_deals=None,
        vinted_scoring=True,
        premium_sources=True,  # Toutes les sources
        alerts_enabled=True,
        favorites_enabled=True,
        export_enabled=True,
    ),
}


# Sources accessibles par tier
# Sources "gratuites" = pas de proxy spécial requis
FREE_SOURCES = {
    "courir",
    "footlocker",
    "size",
    "jdsports",
    "kith",
    "bstn",
    "footpatrol",
    "laredoute",  # Accès direct possible
}

# Sources "premium" = nécessitent Web Unlocker ou proxies spéciaux
PREMIUM_SOURCES = {
    "asos",
    "printemps",
    "galerieslafayette",
    "sns",
    "zalando",
    "vinted",  # Scraping Vinted direct
}


def get_tier_from_plan(plan: Optional[str]) -> SubscriptionTier:
    """Convertit un plan DB en tier."""
    if not plan:
        return SubscriptionTier.FREEMIUM
    return PLAN_TO_TIER.get(plan.lower(), SubscriptionTier.FREEMIUM)


def get_tier_limits(tier: SubscriptionTier) -> TierLimits:
    """Retourne les limites pour un tier."""
    return TIER_LIMITS.get(tier, TIER_LIMITS[SubscriptionTier.FREEMIUM])


def get_allowed_sources(tier: SubscriptionTier) -> Set[str]:
    """Retourne les sources accessibles pour un tier."""
    sources = FREE_SOURCES.copy()
    
    limits = get_tier_limits(tier)
    if limits.premium_sources:
        sources.update(PREMIUM_SOURCES)
    
    return sources


def can_access_source(tier: SubscriptionTier, source: str) -> bool:
    """Vérifie si un tier peut accéder à une source."""
    allowed = get_allowed_sources(tier)
    return source.lower() in allowed


def can_use_vinted_scoring(tier: SubscriptionTier) -> bool:
    """Vérifie si un tier peut utiliser le scoring Vinted."""
    return get_tier_limits(tier).vinted_scoring


def get_max_deals(tier: SubscriptionTier) -> Optional[int]:
    """Retourne le nombre max de deals pour un tier."""
    return get_tier_limits(tier).max_deals


def get_max_top_deals(tier: SubscriptionTier) -> Optional[int]:
    """Retourne le nombre max de top deals pour un tier."""
    return get_tier_limits(tier).max_top_deals


# =============================================================================
# HELPERS POUR L'API
# =============================================================================

def get_tier_info(plan: Optional[str]) -> dict:
    """Retourne les infos complètes d'un tier pour l'API."""
    tier = get_tier_from_plan(plan)
    limits = get_tier_limits(tier)
    
    return {
        "tier": tier.value,
        "plan": plan or "free",
        "limits": {
            "max_deals": limits.max_deals,
            "max_top_deals": limits.max_top_deals,
            "vinted_scoring": limits.vinted_scoring,
            "premium_sources": limits.premium_sources,
            "alerts_enabled": limits.alerts_enabled,
            "favorites_enabled": limits.favorites_enabled,
            "export_enabled": limits.export_enabled,
        },
        "allowed_sources": list(get_allowed_sources(tier)),
    }
