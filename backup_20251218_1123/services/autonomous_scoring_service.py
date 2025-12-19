"""
Autonomous Scoring Service - Score deals WITHOUT Vinted data.

Factors:
1. Discount % (40%) - Higher discount = better deal
2. Brand reputation (25%) - Nike, Jordan, NB = high resale value
3. Model popularity (15%) - Dunk, AF1, Samba = hot models
4. Price sweet spot (10%) - 50-150€ is ideal for resale
5. Size availability (10%) - More sizes = easier to sell
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger


# =============================================================================
# BRAND SCORING - Resale value by brand
# =============================================================================

BRAND_SCORES = {
    # Tier S - Very high resale value (90-100)
    "nike": 95,
    "jordan": 98,
    "air jordan": 98,
    
    # Tier A - High resale value (75-89)
    "new balance": 85,
    "adidas": 80,
    "asics": 78,
    "salomon": 82,
    "on": 75,
    "hoka": 76,
    
    # Tier B - Good resale value (60-74)
    "puma": 65,
    "reebok": 62,
    "converse": 68,
    "vans": 64,
    "saucony": 60,
    
    # Tier C - Average resale (45-59)
    "fila": 50,
    "lacoste": 55,
    "le coq sportif": 48,
    "timberland": 52,
    
    # Tier D - Lower resale (30-44)
    "kappa": 35,
    "ellesse": 38,
    "champion": 40,
    "umbro": 35,
}

# =============================================================================
# MODEL SCORING - Hot models that sell fast
# =============================================================================

HOT_MODELS = {
    # Nike models
    "dunk": 95,
    "dunk low": 98,
    "dunk high": 90,
    "air force": 92,
    "air force 1": 95,
    "af1": 95,
    "air max": 85,
    "air max 1": 90,
    "air max 90": 88,
    "air max 97": 85,
    "air max plus": 82,
    "tn": 82,
    "vapormax": 70,
    "blazer": 78,
    "cortez": 72,
    
    # Jordan models
    "jordan 1": 98,
    "jordan 3": 92,
    "jordan 4": 95,
    "jordan 5": 85,
    "jordan 11": 88,
    "retro": 85,
    
    # Adidas models
    "samba": 95,
    "gazelle": 88,
    "campus": 82,
    "superstar": 75,
    "stan smith": 70,
    "forum": 78,
    "spezial": 85,
    "handball": 80,
    
    # New Balance models
    "550": 92,
    "530": 85,
    "2002r": 88,
    "990": 90,
    "574": 72,
    "327": 78,
    "1906": 80,
    
    # Asics models
    "gel-1130": 88,
    "gel-kayano": 82,
    "gel-nyc": 85,
    
    # Salomon
    "xt-6": 90,
    "xt-4": 85,
    "acs": 82,
}

# =============================================================================
# CATEGORY SCORING
# =============================================================================

CATEGORY_SCORES = {
    "footwear": 85,
    "sneakers": 85,
    "chaussures": 85,
    "shoes": 85,
    "apparel": 55,
    "clothing": 55,
    "vetements": 55,
    "accessories": 40,
    "accessoires": 40,
}

# =============================================================================
# SCORING FUNCTIONS
# =============================================================================

def get_brand_score(brand: str, title: str) -> Tuple[int, str]:
    """Get brand score from brand name or title."""
    text = f"{brand or ''} {title or ''}".lower()
    
    best_score = 50  # Default
    matched_brand = None
    
    for brand_name, score in BRAND_SCORES.items():
        if brand_name in text:
            if score > best_score:
                best_score = score
                matched_brand = brand_name
    
    return best_score, matched_brand


def get_model_score(title: str, model: str = None) -> Tuple[int, str]:
    """Get model score from title."""
    text = f"{title or ''} {model or ''}".lower()
    
    best_score = 50  # Default
    matched_model = None
    
    for model_name, score in HOT_MODELS.items():
        if model_name in text:
            if score > best_score:
                best_score = score
                matched_model = model_name
    
    return best_score, matched_model


def get_discount_score(discount_pct: float) -> int:
    """
    Score based on discount percentage.
    Higher discount = better score.
    """
    if not discount_pct:
        return 30
    
    if discount_pct >= 60:
        return 100
    elif discount_pct >= 50:
        return 95
    elif discount_pct >= 40:
        return 85
    elif discount_pct >= 30:
        return 75
    elif discount_pct >= 25:
        return 65
    elif discount_pct >= 20:
        return 55
    elif discount_pct >= 15:
        return 45
    else:
        return 35


def get_price_score(price: float) -> int:
    """
    Score based on price sweet spot.
    50-150€ is ideal for resale (easy to sell, good margin).
    """
    if not price:
        return 50
    
    if 60 <= price <= 120:
        return 100  # Perfect sweet spot
    elif 50 <= price <= 150:
        return 90
    elif 40 <= price <= 180:
        return 75
    elif 30 <= price <= 200:
        return 60
    elif price < 30:
        return 40  # Too cheap, might be low quality
    else:
        return 50  # Expensive, harder to sell


def get_size_score(sizes: List[str]) -> int:
    """
    Score based on size availability.
    More sizes = easier to sell.
    """
    if not sizes:
        return 50
    
    n = len(sizes)
    if n >= 10:
        return 100
    elif n >= 7:
        return 90
    elif n >= 5:
        return 80
    elif n >= 3:
        return 70
    elif n >= 1:
        return 60
    else:
        return 40


def get_category_score(category: str, title: str) -> int:
    """Get category score."""
    text = f"{category or ''} {title or ''}".lower()
    
    for cat_name, score in CATEGORY_SCORES.items():
        if cat_name in text:
            return score
    
    # Default - assume footwear if not specified
    if any(word in text for word in ["shoe", "sneaker", "basket", "running", "trainer"]):
        return 80
    
    return 60


def calculate_autonomous_score(
    title: str,
    brand: str = None,
    model: str = None,
    category: str = None,
    discount_pct: float = None,
    price: float = None,
    sizes: List[str] = None,
) -> Dict[str, Any]:
    """
    Calculate deal score WITHOUT Vinted data.
    
    Weights:
    - Discount: 40%
    - Brand: 25%
    - Model: 15%
    - Price: 10%
    - Sizes: 10%
    
    Returns score and breakdown.
    """
    
    # Calculate individual scores
    discount_score = get_discount_score(discount_pct)
    brand_score, matched_brand = get_brand_score(brand, title)
    model_score, matched_model = get_model_score(title, model)
    price_score = get_price_score(price)
    size_score = get_size_score(sizes)
    category_score = get_category_score(category, title)
    
    # Apply weights
    weighted_score = (
        discount_score * 0.40 +
        brand_score * 0.25 +
        model_score * 0.15 +
        price_score * 0.10 +
        size_score * 0.10
    )
    
    # Bonus for hot combinations
    bonus = 0
    bonus_reasons = []
    
    # Nike/Jordan Dunk/Jordan 1 = +5
    if matched_brand in ["nike", "jordan"] and matched_model in ["dunk", "dunk low", "jordan 1", "jordan 4"]:
        bonus += 5
        bonus_reasons.append("Hot combo")
    
    # Adidas Samba/Gazelle = +5
    if matched_brand == "adidas" and matched_model in ["samba", "gazelle", "spezial"]:
        bonus += 5
        bonus_reasons.append("Trending model")
    
    # Big discount on hot brand = +3
    if discount_pct and discount_pct >= 40 and brand_score >= 80:
        bonus += 3
        bonus_reasons.append("Big discount on premium brand")
    
    # Apply bonus (cap at 100)
    flip_score = min(100, weighted_score + bonus)
    
    # Determine action
    if flip_score >= 75:
        action = "BUY"
        confidence = 0.85
    elif flip_score >= 60:
        action = "WATCH"
        confidence = 0.70
    else:
        action = "IGNORE"
        confidence = 0.60
    
    # Estimate margin (rough estimate based on discount)
    estimated_margin_pct = None
    if discount_pct:
        # Assume can sell at ~70-80% of original price on Vinted
        # Margin = (sell_price - buy_price) / buy_price
        # If discount is 40%, buy at 60%, sell at 75% of original = 25% margin
        estimated_margin_pct = round(discount_pct * 0.6, 1)  # Rough estimate
    
    # Build explanation
    explanation_parts = []
    if matched_brand:
        explanation_parts.append(f"{matched_brand.title()} has good resale value")
    if matched_model:
        explanation_parts.append(f"{matched_model.title()} is a popular model")
    if discount_pct and discount_pct >= 30:
        explanation_parts.append(f"{discount_pct:.0f}% discount is attractive")
    if bonus_reasons:
        explanation_parts.extend(bonus_reasons)
    
    explanation = ". ".join(explanation_parts) if explanation_parts else "Standard deal"
    
    # Risks
    risks = []
    if brand_score < 60:
        risks.append("Brand has lower resale value")
    if not sizes or len(sizes) < 3:
        risks.append("Limited size availability")
    if price and price > 150:
        risks.append("Higher price point may be harder to sell")
    if discount_pct and discount_pct < 25:
        risks.append("Low discount reduces margin potential")
    
    return {
        "flip_score": round(flip_score, 1),
        "discount_score": discount_score,
        "brand_score": brand_score,
        "model_score": model_score,
        "price_score": price_score,
        "size_score": size_score,
        "recommended_action": action,
        "confidence": confidence,
        "explanation": explanation,
        "explanation_short": f"{action}: {explanation[:50]}..." if len(explanation) > 50 else f"{action}: {explanation}",
        "risks": risks,
        "estimated_margin_pct": estimated_margin_pct,
        "matched_brand": matched_brand,
        "matched_model": matched_model,
        "bonus_applied": bonus,
        "score_breakdown": {
            "discount": {"score": discount_score, "weight": 0.40},
            "brand": {"score": brand_score, "weight": 0.25, "matched": matched_brand},
            "model": {"score": model_score, "weight": 0.15, "matched": matched_model},
            "price": {"score": price_score, "weight": 0.10},
            "sizes": {"score": size_score, "weight": 0.10},
            "bonus": bonus,
        },
        "model_version": "autonomous_v1",
    }


def score_deal_autonomous(deal_data: Dict) -> Dict[str, Any]:
    """
    Score a deal using autonomous scoring (no Vinted).
    
    Args:
        deal_data: Dict with title, brand, model, category, discount_percent, price, sizes_available
    
    Returns:
        Score result dict
    """
    return calculate_autonomous_score(
        title=deal_data.get("title") or deal_data.get("product_name", ""),
        brand=deal_data.get("brand"),
        model=deal_data.get("model"),
        category=deal_data.get("category"),
        discount_pct=deal_data.get("discount_percent") or deal_data.get("discount_pct"),
        price=deal_data.get("price") or deal_data.get("sale_price"),
        sizes=deal_data.get("sizes_available"),
    )
