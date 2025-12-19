"""
Subscription Tiers - Définition des niveaux d'abonnement et leurs limitations.

1. FREEMIUM (free, freemium, anonymous):
   ├── 5 deals max (1 top + 4 autres)
   ├── Sources basiques (sans proxy spécial)
   ├── Pas de Vinted scoring
   └── Pas d'alertes/favoris/export

2. BASIC (basic):
   ├── Deals illimités
   ├── Sources basiques (pas de web unlocker/proxies premium)
   ├── Vinted scoring
   ├── Alertes et favoris
   └── Pas d'export

3. PREMIUM (premium):
   ├── Deals illimités
   ├── Toutes les sources (+ protégées: asos, printemps, etc.)
   ├── Vinted scoring
   ├── Alertes, favoris, export
   └── Accès prioritaire aux nouveaux deals

4. ADMIN (admin, owner):
   ├── Tous les droits PREMIUM
   ├── Console d'administration
   ├── Gestion des scrapers
   ├── Statistiques système
   └── Gestion des utilisateurs
"""
from enum import Enum
from typing import Set, List, Optional
from dataclasses import dataclass


class SubscriptionTier(str, Enum):
    """Niveaux d'abonnement."""
    FREEMIUM = "freemium"
    BASIC = "basic"
    PREMIUM = "premium"
    ADMIN = "admin"


# Mapping des plans DB vers les tiers
PLAN_TO_TIER = {
    # Freemium (gratuit)
    "free": SubscriptionTier.FREEMIUM,
    "freemium": SubscriptionTier.FREEMIUM,
    None: SubscriptionTier.FREEMIUM,
    "": SubscriptionTier.FREEMIUM,

    # Basic (abonnement entrée de gamme)
    "basic": SubscriptionTier.BASIC,

    # Premium (abonnement complet)
    "premium": SubscriptionTier.PREMIUM,
    "pro": SubscriptionTier.PREMIUM,

    # Admin (propriétaire + administrateurs)
    "admin": SubscriptionTier.ADMIN,
    "owner": SubscriptionTier.ADMIN,
    "agency": SubscriptionTier.ADMIN,  # Legacy - traité comme admin
}


@dataclass
class TierLimits:
    """Limitations par tier."""
    max_deals: Optional[int]  # None = illimité
    max_top_deals: Optional[int]  # Nombre de deals "top" visibles
    vinted_scoring: bool  # Accès au scoring Vinted
    premium_sources: bool  # Accès aux sources protégées (web unlocker)
    alerts_enabled: bool  # Alertes personnalisées
    favorites_enabled: bool  # Favoris
    export_enabled: bool  # Export CSV/JSON
    admin_access: bool  # Accès console admin


# Configuration des limites par tier
TIER_LIMITS = {
    # 1. FREEMIUM - Gratuit, limité
    SubscriptionTier.FREEMIUM: TierLimits(
        max_deals=5,
        max_top_deals=1,
        vinted_scoring=False,
        premium_sources=False,
        alerts_enabled=False,
        favorites_enabled=False,
        export_enabled=False,
        admin_access=False,
    ),

    # 2. BASIC - Abonnement entrée de gamme (illimité, sources basiques)
    SubscriptionTier.BASIC: TierLimits(
        max_deals=None,  # Illimité
        max_top_deals=None,
        vinted_scoring=True,
        premium_sources=False,  # Pas d'accès aux sources web unlocker
        alerts_enabled=True,
        favorites_enabled=True,
        export_enabled=False,
        admin_access=False,
    ),

    # 3. PREMIUM - Abonnement complet
    SubscriptionTier.PREMIUM: TierLimits(
        max_deals=None,  # Illimité
        max_top_deals=None,
        vinted_scoring=True,
        premium_sources=True,
        alerts_enabled=True,
        favorites_enabled=True,
        export_enabled=True,
        admin_access=False,
    ),

    # 4. ADMIN - Droits premium + console admin
    SubscriptionTier.ADMIN: TierLimits(
        max_deals=None,  # Illimité
        max_top_deals=None,
        vinted_scoring=True,
        premium_sources=True,
        alerts_enabled=True,
        favorites_enabled=True,
        export_enabled=True,
        admin_access=True,
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
    "laredoute",
}

# Sources "premium" = nécessitent Web Unlocker ou proxies spéciaux
PREMIUM_SOURCES = {
    "asos",
    "printemps",
    "galerieslafayette",
    "sns",
    "zalando",
    "vinted",
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


def is_admin(tier: SubscriptionTier) -> bool:
    """Vérifie si le tier a accès à la console admin."""
    return get_tier_limits(tier).admin_access


def is_admin_plan(plan: Optional[str]) -> bool:
    """Vérifie si un plan a accès à la console admin."""
    tier = get_tier_from_plan(plan)
    return is_admin(tier)


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
            "admin_access": limits.admin_access,
        },
        "allowed_sources": list(get_allowed_sources(tier)),
    }
