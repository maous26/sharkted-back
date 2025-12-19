"""
SharkScore - Scoring simplifié basé sur les critères RÉALISTES d'un vendeur pro.

Critères fondamentaux:
1. MARGE NETTE (35%) - Écart réel entre achat et revente Vinted
2. LIQUIDITÉ (30%) - Vitesse de vente réelle (pas annonces fantômes)
3. PROFONDEUR TAILLES (20%) - Plus de tailles = moins de risque
4. ROTATION/TIMING (15%) - Fraîcheur du deal, saturation

Règle d'or: prix_achat + frictions < prix_revente_réaliste
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger


# =============================================================================
# DONNÉES DE MARCHÉ - Resale multipliers basés sur données réelles
# =============================================================================

# Multiplicateurs de revente par marque (% du prix retail sur Vinted)
BRAND_RESALE_DATA = {
    # Tier S - Se revend au-dessus ou proche du retail (0.85-1.0+)
    "jordan": {"multiplier": 0.95, "liquidity_days": 5, "demand": "high"},
    "nike": {"multiplier": 0.85, "liquidity_days": 7, "demand": "high"},
    "yeezy": {"multiplier": 0.90, "liquidity_days": 7, "demand": "high"},

    # Tier A - Bonne revente (0.75-0.84)
    "new balance": {"multiplier": 0.80, "liquidity_days": 10, "demand": "medium-high"},
    "adidas": {"multiplier": 0.78, "liquidity_days": 10, "demand": "medium-high"},
    "asics": {"multiplier": 0.75, "liquidity_days": 12, "demand": "medium"},
    "salomon": {"multiplier": 0.80, "liquidity_days": 10, "demand": "medium-high"},
    "on running": {"multiplier": 0.72, "liquidity_days": 14, "demand": "medium"},
    "hoka": {"multiplier": 0.70, "liquidity_days": 14, "demand": "medium"},

    # Tier B - Revente correcte (0.60-0.74)
    "puma": {"multiplier": 0.65, "liquidity_days": 15, "demand": "medium"},
    "reebok": {"multiplier": 0.60, "liquidity_days": 18, "demand": "low-medium"},
    "converse": {"multiplier": 0.68, "liquidity_days": 12, "demand": "medium"},
    "vans": {"multiplier": 0.62, "liquidity_days": 14, "demand": "medium"},
    "saucony": {"multiplier": 0.58, "liquidity_days": 18, "demand": "low-medium"},

    # Tier C - Revente difficile (0.45-0.59)
    "fila": {"multiplier": 0.50, "liquidity_days": 25, "demand": "low"},
    "lacoste": {"multiplier": 0.55, "liquidity_days": 20, "demand": "low-medium"},
    "timberland": {"multiplier": 0.52, "liquidity_days": 22, "demand": "low-medium"},
    "kappa": {"multiplier": 0.40, "liquidity_days": 30, "demand": "low"},
}

# Modèles qui se vendent vite (bonus liquidité)
HOT_MODELS = {
    # Nike - très liquides
    "dunk": {"liquidity_bonus": 0.15, "price_premium": 0.10},
    "dunk low": {"liquidity_bonus": 0.18, "price_premium": 0.12},
    "air force 1": {"liquidity_bonus": 0.12, "price_premium": 0.08},
    "jordan 1": {"liquidity_bonus": 0.20, "price_premium": 0.15},
    "jordan 4": {"liquidity_bonus": 0.18, "price_premium": 0.12},
    "air max 1": {"liquidity_bonus": 0.10, "price_premium": 0.05},
    "air max 90": {"liquidity_bonus": 0.08, "price_premium": 0.03},

    # Adidas - trending
    "samba": {"liquidity_bonus": 0.20, "price_premium": 0.15},
    "gazelle": {"liquidity_bonus": 0.15, "price_premium": 0.10},
    "campus": {"liquidity_bonus": 0.10, "price_premium": 0.05},
    "spezial": {"liquidity_bonus": 0.12, "price_premium": 0.08},

    # New Balance - steady sellers
    "550": {"liquidity_bonus": 0.15, "price_premium": 0.10},
    "2002r": {"liquidity_bonus": 0.12, "price_premium": 0.08},
    "530": {"liquidity_bonus": 0.10, "price_premium": 0.05},
    "990": {"liquidity_bonus": 0.15, "price_premium": 0.10},

    # Asics
    "gel-1130": {"liquidity_bonus": 0.12, "price_premium": 0.08},
    "gel-nyc": {"liquidity_bonus": 0.10, "price_premium": 0.06},

    # Salomon
    "xt-6": {"liquidity_bonus": 0.15, "price_premium": 0.10},
}

# Frais de plateforme réalistes (Vinted)
PLATFORM_FEES = {
    "commission": 0.05,      # 5% commission Vinted
    "protection": 0.03,      # 3% protection acheteur
    "payment": 0.03,         # ~3% frais PayPal/carte
}
SHIPPING_COST = 5.50  # Mondial Relay moyen


# =============================================================================
# FONCTIONS DE SCORING
# =============================================================================

def get_brand_data(brand: str, title: str) -> Dict:
    """Récupère les données de revente pour une marque."""
    text = f"{brand or ''} {title or ''}".lower()

    for brand_name, data in BRAND_RESALE_DATA.items():
        if brand_name in text:
            return {"name": brand_name, **data}

    # Marque inconnue = tier C par défaut
    return {
        "name": "unknown",
        "multiplier": 0.50,
        "liquidity_days": 25,
        "demand": "low"
    }


def get_model_data(title: str, model: str = None) -> Optional[Dict]:
    """Récupère les données de liquidité pour un modèle hot."""
    text = f"{title or ''} {model or ''}".lower()

    for model_name, data in HOT_MODELS.items():
        if model_name in text:
            return {"name": model_name, **data}

    return None


def calculate_real_margin(
    sale_price: float,
    original_price: float,
    brand_data: Dict,
    model_data: Optional[Dict] = None
) -> Dict:
    """
    Calcule la marge RÉELLE nette après frais.

    Formule:
    Prix revente = original_price × brand_multiplier × (1 + model_premium)
    Frais = (commission + protection + payment) × prix_revente + shipping
    Marge nette = prix_revente - sale_price - frais
    """

    # Prix de revente estimé sur Vinted
    multiplier = brand_data["multiplier"]
    model_premium = model_data["price_premium"] if model_data else 0

    estimated_resale = original_price * multiplier * (1 + model_premium)

    # Calcul des frais
    total_fee_rate = sum(PLATFORM_FEES.values())
    fees = (estimated_resale * total_fee_rate) + SHIPPING_COST

    # Marge nette
    net_margin_euro = estimated_resale - sale_price - fees
    net_margin_pct = (net_margin_euro / sale_price * 100) if sale_price > 0 else 0

    return {
        "estimated_resale": round(estimated_resale, 2),
        "fees": round(fees, 2),
        "net_margin_euro": round(net_margin_euro, 2),
        "net_margin_pct": round(net_margin_pct, 1),
        "is_profitable": net_margin_euro >= 20,  # Minimum 20€ net
    }


def calculate_margin_score(margin_data: Dict) -> int:
    """
    Score de marge basé sur le profit NET réel.

    Règle: minimum 20-30€ net pour que ça vaille le coup.
    """
    net_euro = margin_data["net_margin_euro"]
    net_pct = margin_data["net_margin_pct"]

    if net_euro < 0:
        return 0  # Perte
    elif net_euro < 10:
        return 20  # Pas rentable
    elif net_euro < 20:
        return 40  # Limite
    elif net_euro < 30:
        return 60  # Correct
    elif net_euro < 50:
        return 80  # Bon
    else:
        return 100  # Excellent


def calculate_liquidity_score(
    brand_data: Dict,
    model_data: Optional[Dict],
    vinted_stats: Optional[Dict] = None
) -> int:
    """
    Score de liquidité - à quelle vitesse ça se vend.

    Sources:
    - Données Vinted réelles si disponibles
    - Sinon estimation basée sur marque/modèle
    """

    # Si on a des stats Vinted réelles, les utiliser
    if vinted_stats and vinted_stats.get("sales_last_30_days"):
        sales = vinted_stats["sales_last_30_days"]
        if sales >= 20:
            return 100  # Très liquide
        elif sales >= 10:
            return 85
        elif sales >= 5:
            return 70
        elif sales >= 2:
            return 55
        else:
            return 40

    # Sinon estimation basée sur marque + modèle
    base_days = brand_data["liquidity_days"]

    # Bonus modèle hot
    if model_data:
        base_days = max(3, base_days - (model_data["liquidity_bonus"] * 30))

    # Convertir en score (moins de jours = meilleur score)
    if base_days <= 5:
        return 100
    elif base_days <= 10:
        return 85
    elif base_days <= 15:
        return 70
    elif base_days <= 20:
        return 55
    else:
        return 40


def calculate_size_depth_score(sizes: List[str]) -> int:
    """
    Score de profondeur des tailles.

    5 tailles vendables > 1 taille à grosse marge
    Plus de tailles = moins de risque, plus de chances de vente.
    """
    if not sizes:
        return 30  # Risqué

    n = len(sizes)

    # Filtrer les tailles "bizarres" (kids, XXS, XXL extrêmes)
    standard_sizes = []
    for s in sizes:
        s_lower = str(s).lower()
        # Exclure les tailles enfant et extrêmes
        if not any(x in s_lower for x in ["c", "y", "kid", "enfant", "xxs", "4xl", "5xl"]):
            standard_sizes.append(s)

    n_standard = len(standard_sizes)

    if n_standard >= 8:
        return 100  # Très profond
    elif n_standard >= 6:
        return 90
    elif n_standard >= 4:
        return 80
    elif n_standard >= 3:
        return 70
    elif n_standard >= 2:
        return 55
    elif n_standard >= 1:
        return 40
    else:
        return 20  # Que des tailles bizarres


def calculate_timing_score(
    detected_hours_ago: float = 0,
    is_public_deal: bool = False,
    discount_pct: float = 0
) -> int:
    """
    Score de timing/rotation.

    - Deal frais = meilleur
    - Deal trop partagé = risque saturation
    - Gros discount = peut-être un piège (fin de série)
    """
    score = 70  # Base

    # Fraîcheur du deal
    if detected_hours_ago < 1:
        score += 20  # Très frais
    elif detected_hours_ago < 6:
        score += 10
    elif detected_hours_ago < 24:
        score += 0
    else:
        score -= 10  # Deal ancien

    # Saturation potentielle (si deal public/viral)
    if is_public_deal:
        score -= 15

    # Très gros discount = méfiance (fin de série, défaut?)
    if discount_pct >= 70:
        score -= 10  # Pourquoi si soldé?
    elif discount_pct >= 50:
        score += 5  # Sweet spot

    return max(0, min(100, score))


def calculate_shark_score(
    title: str,
    brand: str = None,
    model: str = None,
    sale_price: float = None,
    original_price: float = None,
    discount_pct: float = None,
    sizes: List[str] = None,
    vinted_stats: Dict = None,
    detected_hours_ago: float = 0,
    is_public_deal: bool = False,
) -> Dict[str, Any]:
    """
    Calcule le SharkScore final.

    Pondération:
    - Marge nette: 35%
    - Liquidité: 30%
    - Profondeur tailles: 20%
    - Timing: 15%
    """

    # Récupérer données marque/modèle
    brand_data = get_brand_data(brand, title)
    model_data = get_model_data(title, model)

    # Calculer le prix original si pas fourni
    if not original_price and sale_price and discount_pct and discount_pct < 100:
        original_price = sale_price / (1 - discount_pct / 100)
    elif not original_price:
        original_price = sale_price * 1.5 if sale_price else 100

    # Calculs individuels
    margin_data = calculate_real_margin(
        sale_price or 0,
        original_price,
        brand_data,
        model_data
    )

    margin_score = calculate_margin_score(margin_data)
    liquidity_score = calculate_liquidity_score(brand_data, model_data, vinted_stats)
    size_score = calculate_size_depth_score(sizes or [])
    timing_score = calculate_timing_score(detected_hours_ago, is_public_deal, discount_pct or 0)

    # Score final pondéré
    shark_score = (
        margin_score * 0.35 +
        liquidity_score * 0.30 +
        size_score * 0.20 +
        timing_score * 0.15
    )

    # Ajustements
    # Malus si marge négative
    if margin_data["net_margin_euro"] < 0:
        shark_score = min(shark_score, 30)

    # Bonus si combo parfait (marge > 30€ + liquide + plusieurs tailles)
    if margin_data["net_margin_euro"] >= 30 and liquidity_score >= 80 and size_score >= 70:
        shark_score = min(100, shark_score + 5)

    shark_score = round(shark_score, 1)

    # Recommandation
    if shark_score >= 75:
        action = "BUY"
        confidence = 0.85
    elif shark_score >= 55:
        action = "WATCH"
        confidence = 0.70
    else:
        action = "PASS"
        confidence = 0.60

    # Risques identifiés
    risks = []
    if margin_data["net_margin_euro"] < 20:
        risks.append(f"Marge faible ({margin_data['net_margin_euro']:.0f}€ net)")
    if liquidity_score < 60:
        risks.append(f"Liquidité moyenne ({brand_data['liquidity_days']}j estimé)")
    if size_score < 60:
        risks.append("Peu de tailles standard")
    if discount_pct and discount_pct >= 70:
        risks.append("Discount très élevé - vérifier stock/qualité")

    # Explication courte
    explanation_parts = []
    if margin_data["net_margin_euro"] >= 30:
        explanation_parts.append(f"+{margin_data['net_margin_euro']:.0f}€ net")
    if model_data:
        explanation_parts.append(f"Modèle {model_data['name'].title()} recherché")
    if liquidity_score >= 80:
        explanation_parts.append("Vente rapide")

    explanation = " | ".join(explanation_parts) if explanation_parts else "Deal standard"

    return {
        "shark_score": shark_score,
        "flip_score": shark_score,  # Alias pour compatibilité

        # Breakdown détaillé
        "margin_score": margin_score,
        "liquidity_score": liquidity_score,
        "size_score": size_score,
        "timing_score": timing_score,

        # Données de marge
        "estimated_resale": margin_data["estimated_resale"],
        "recommended_price": margin_data["estimated_resale"],
        "net_margin_euro": margin_data["net_margin_euro"],
        "net_margin_pct": margin_data["net_margin_pct"],
        "estimated_margin_pct": margin_data["net_margin_pct"],
        "estimated_margin_euro": margin_data["net_margin_euro"],
        "fees": margin_data["fees"],
        "is_profitable": margin_data["is_profitable"],

        # Liquidité
        "estimated_sell_days": brand_data["liquidity_days"],
        "demand_level": brand_data["demand"],

        # Recommandation
        "recommended_action": action,
        "confidence": confidence,

        # Explications
        "explanation": explanation,
        "explanation_short": f"{action}: {explanation}",
        "risks": risks,

        # Détails
        "matched_brand": brand_data["name"],
        "matched_model": model_data["name"] if model_data else None,

        # Score breakdown pour UI
        "score_breakdown": {
            "margin": {"score": margin_score, "weight": 0.35, "value": margin_data["net_margin_euro"]},
            "liquidity": {"score": liquidity_score, "weight": 0.30, "days": brand_data["liquidity_days"]},
            "sizes": {"score": size_score, "weight": 0.20, "count": len(sizes or [])},
            "timing": {"score": timing_score, "weight": 0.15},
        },

        "model_version": "shark_v1",
    }


def score_deal_shark(deal_data: Dict) -> Dict[str, Any]:
    """
    Point d'entrée principal pour scorer un deal.

    Args:
        deal_data: Dict avec les champs du deal

    Returns:
        Score result dict avec SharkScore
    """
    return calculate_shark_score(
        title=deal_data.get("title") or deal_data.get("product_name", ""),
        brand=deal_data.get("brand"),
        model=deal_data.get("model"),
        sale_price=deal_data.get("sale_price") or deal_data.get("price"),
        original_price=deal_data.get("original_price"),
        discount_pct=deal_data.get("discount_percent") or deal_data.get("discount_pct"),
        sizes=deal_data.get("sizes_available"),
        vinted_stats=deal_data.get("vinted_stats"),
        detected_hours_ago=0,  # À calculer si date disponible
        is_public_deal=False,
    )
