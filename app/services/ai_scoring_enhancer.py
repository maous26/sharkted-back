"""
AI Scoring Enhancer - Renforce la fiabilité du scoring avec Claude
Combine 3 approches:
1. Classification du modèle (hype/basique/collab/limited)
2. Validation des prix (cohérence, anomalies)
3. Ajustement dynamique du score basé sur l'analyse

Utilise Claude Haiku pour un coût minimal (~0.00025$/requête)
"""

import os
import re
import json
import hashlib
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from loguru import logger


# =============================================================================
# CACHE POUR ÉVITER LES APPELS RÉPÉTÉS
# =============================================================================
_ai_cache: Dict[str, Dict[str, Any]] = {}
_cache_expiry: Dict[str, datetime] = {}
CACHE_TTL_HOURS = 24  # Cache valide 24h


def _get_cache_key(product_name: str, brand: str, price: float) -> str:
    content = f"{product_name}|{brand}|{int(price)}".lower()
    return hashlib.md5(content.encode()).hexdigest()


def _is_cache_valid(key: str) -> bool:
    if key not in _cache_expiry:
        return False
    return datetime.utcnow() < _cache_expiry[key]


# =============================================================================
# MODÈLES CONNUS (évite les appels IA pour les cas évidents)
# =============================================================================

# Modèles HYPE connus - boost automatique
HYPE_MODELS = {
    # Nike/Jordan
    "travis scott", "off-white", "off white", "dior", "fragment",
    "union", "sacai", "ambush", "undercover", "cpfm",
    "fear of god", "fog", "clot", "peaceminusone", "g-dragon",
    # Adidas/Yeezy
    "yeezy", "pharrell", "human race", "bad bunny", "wales bonner",
    "jerry lorenzo", "prada",
    # New Balance
    "jjjjound", "aime leon dore", "ald", "kith", "concepts",
    "joe freshgoods", "stussy", "wtaps",
    # Autres
    "supreme", "bape", "a bathing ape",
}

# Modèles BASIQUES - pas de boost
BASIC_MODELS = {
    "essential", "basic", "classic", "leather", "canvas",
    "core", "base", "standard", "original",
}

# Coloris SAFE (faciles à revendre)
SAFE_COLORS = {
    "black", "noir", "white", "blanc", "grey", "gray", "gris",
    "navy", "cream", "beige", "brown", "marron",
}

# Coloris RISQUÉS
RISKY_COLORS = {
    "pink", "rose", "yellow", "jaune", "orange", "purple",
    "violet", "neon", "fluo", "lime", "turquoise",
}


# =============================================================================
# ANALYSE RAPIDE SANS IA (règles)
# =============================================================================

def quick_analysis(product_name: str, brand: str) -> Dict[str, Any]:
    """Analyse rapide basée sur des règles (sans appel IA)."""
    name_lower = product_name.lower()
    brand_lower = (brand or "").lower()

    result = {
        "is_hype": False,
        "is_collab": False,
        "is_basic": False,
        "is_limited": False,
        "color_risk": "neutral",
        "confidence": 0.6,  # Confiance moyenne pour règles
        "adjustments": [],
        "method": "rules",
    }

    # Détection HYPE
    for hype in HYPE_MODELS:
        if hype in name_lower:
            result["is_hype"] = True
            result["is_collab"] = True
            result["adjustments"].append(f"Collab détectée: {hype}")
            result["confidence"] = 0.85
            break

    # Détection BASIQUE
    for basic in BASIC_MODELS:
        if basic in name_lower:
            result["is_basic"] = True
            result["adjustments"].append(f"Modèle basique: {basic}")
            break

    # Détection LIMITED
    limited_keywords = ["limited", "exclusive", "special", "anniversary", "retro og"]
    for kw in limited_keywords:
        if kw in name_lower:
            result["is_limited"] = True
            result["adjustments"].append(f"Édition limitée: {kw}")
            result["confidence"] = 0.75
            break

    # Analyse couleur
    for safe in SAFE_COLORS:
        if safe in name_lower:
            result["color_risk"] = "safe"
            break
    for risky in RISKY_COLORS:
        if risky in name_lower:
            result["color_risk"] = "risky"
            result["adjustments"].append(f"Coloris risqué: {risky}")
            break

    return result


# =============================================================================
# ANALYSE IA APPROFONDIE
# =============================================================================

async def ai_deep_analysis(
    product_name: str,
    brand: str,
    original_price: float,
    sale_price: float,
    discount_pct: float,
) -> Dict[str, Any]:
    """
    Analyse approfondie avec Claude Haiku.
    Évalue le potentiel de revente et la fiabilité des données.
    """
    cache_key = _get_cache_key(product_name, brand, sale_price)

    # Check cache
    if cache_key in _ai_cache and _is_cache_valid(cache_key):
        cached = _ai_cache[cache_key].copy()
        cached["method"] = "cache"
        return cached

    # Fallback sur règles si pas d'API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("No ANTHROPIC_API_KEY, using rules-based analysis")
        result = quick_analysis(product_name, brand)
        _ai_cache[cache_key] = result
        _cache_expiry[cache_key] = datetime.utcnow() + timedelta(hours=CACHE_TTL_HOURS)
        return result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Analyse ce produit sneaker/streetwear pour évaluer son potentiel de revente.

PRODUIT:
- Nom: {product_name}
- Marque: {brand}
- Prix original: {original_price}€
- Prix soldé: {sale_price}€
- Réduction: {discount_pct:.0f}%

Réponds UNIQUEMENT en JSON valide (pas de markdown, pas de commentaires):
{{
  "model_type": "hype|collab|limited|standard|basic",
  "resale_potential": 1-100,
  "price_coherence": 1-100,
  "demand_level": "very_high|high|medium|low|very_low",
  "color_analysis": "safe|neutral|risky",
  "is_authentic_deal": true|false,
  "score_adjustment": -20 to +30,
  "confidence": 0.0-1.0,
  "reasoning": "explication courte"
}}

RÈGLES D'ÉVALUATION:
- model_type: "hype" si collab connue (Travis, Off-White...), "limited" si édition spéciale, "basic" si modèle entrée de gamme
- resale_potential: 80+ pour hype/collab, 50-70 pour standard, <50 pour basic
- price_coherence: 90+ si prix normal pour ce modèle, <70 si suspect
- is_authentic_deal: false si prix trop bas (fake?) ou trop haut (pas un deal)
- score_adjustment: +20/+30 pour hype, 0 pour standard, -10/-20 pour basic ou risqué"""

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()

        # Nettoyer le JSON si nécessaire
        if response_text.startswith("```"):
            response_text = re.sub(r'^```\w*\n?', '', response_text)
            response_text = re.sub(r'\n?```$', '', response_text)

        data = json.loads(response_text)

        result = {
            "model_type": data.get("model_type", "standard"),
            "resale_potential": data.get("resale_potential", 50),
            "price_coherence": data.get("price_coherence", 80),
            "demand_level": data.get("demand_level", "medium"),
            "color_analysis": data.get("color_analysis", "neutral"),
            "is_authentic_deal": data.get("is_authentic_deal", True),
            "score_adjustment": data.get("score_adjustment", 0),
            "confidence": data.get("confidence", 0.8),
            "reasoning": data.get("reasoning", ""),
            "method": "ai",
            # Mappings pour compatibilité
            "is_hype": data.get("model_type") in ["hype", "collab"],
            "is_collab": data.get("model_type") == "collab",
            "is_limited": data.get("model_type") == "limited",
            "is_basic": data.get("model_type") == "basic",
            "color_risk": data.get("color_analysis", "neutral"),
        }

        # Cache le résultat
        _ai_cache[cache_key] = result
        _cache_expiry[cache_key] = datetime.utcnow() + timedelta(hours=CACHE_TTL_HOURS)

        logger.info(f"AI analysis: {product_name[:40]}... -> {result['model_type']}, adj={result['score_adjustment']}")

        return result

    except Exception as e:
        logger.warning(f"AI analysis failed, using rules: {e}")
        result = quick_analysis(product_name, brand)
        _ai_cache[cache_key] = result
        _cache_expiry[cache_key] = datetime.utcnow() + timedelta(hours=CACHE_TTL_HOURS)
        return result


# =============================================================================
# FONCTION PRINCIPALE D'ENRICHISSEMENT DU SCORE
# =============================================================================

async def enhance_flip_score(
    base_score: float,
    product_name: str,
    brand: str,
    original_price: float,
    sale_price: float,
    discount_pct: float,
    use_ai: bool = True,
) -> Tuple[float, Dict[str, Any]]:
    """
    Enrichit le FlipScore avec l'analyse IA.

    Args:
        base_score: Score calculé par le moteur autonome
        product_name: Nom du produit
        brand: Marque
        original_price: Prix original
        sale_price: Prix soldé
        discount_pct: Pourcentage de réduction
        use_ai: Si True, utilise Claude pour analyse approfondie

    Returns:
        (enhanced_score, analysis_details)
    """
    # Analyse rapide d'abord
    quick = quick_analysis(product_name, brand)

    # Si pas d'IA ou produit clairement identifiable, utilise règles
    if not use_ai or quick["is_hype"] or quick["is_basic"]:
        adjustment = 0

        if quick["is_hype"] or quick["is_collab"]:
            adjustment += 25  # Gros boost pour hype
        elif quick["is_limited"]:
            adjustment += 15  # Boost modéré pour limited
        elif quick["is_basic"]:
            adjustment -= 10  # Malus pour basique

        if quick["color_risk"] == "safe":
            adjustment += 5
        elif quick["color_risk"] == "risky":
            adjustment -= 10

        enhanced_score = min(max(base_score + adjustment, 0), 100)

        return enhanced_score, {
            "base_score": base_score,
            "adjustment": adjustment,
            "enhanced_score": enhanced_score,
            "analysis": quick,
            "method": "rules",
        }

    # Analyse IA approfondie
    analysis = await ai_deep_analysis(
        product_name, brand, original_price, sale_price, discount_pct
    )

    # Calcul de l'ajustement
    adjustment = analysis.get("score_adjustment", 0)

    # Ajustements additionnels basés sur l'analyse
    if not analysis.get("is_authentic_deal", True):
        adjustment -= 15  # Pénalité si deal suspect

    if analysis.get("price_coherence", 80) < 60:
        adjustment -= 10  # Pénalité si prix incohérent

    # Appliquer l'ajustement avec la confiance de l'IA
    confidence = analysis.get("confidence", 0.8)
    weighted_adjustment = adjustment * confidence

    enhanced_score = min(max(base_score + weighted_adjustment, 0), 100)

    return round(enhanced_score, 1), {
        "base_score": base_score,
        "adjustment": round(weighted_adjustment, 1),
        "raw_adjustment": adjustment,
        "enhanced_score": round(enhanced_score, 1),
        "analysis": analysis,
        "method": analysis.get("method", "ai"),
    }


# =============================================================================
# UTILITAIRES
# =============================================================================

def get_cache_stats() -> Dict[str, Any]:
    """Statistiques du cache IA."""
    valid_count = sum(1 for k in _ai_cache if _is_cache_valid(k))
    ai_count = sum(1 for v in _ai_cache.values() if v.get("method") == "ai")

    return {
        "total_cached": len(_ai_cache),
        "valid_cached": valid_count,
        "ai_analyses": ai_count,
        "rules_analyses": len(_ai_cache) - ai_count,
    }


def clear_expired_cache():
    """Nettoie les entrées expirées du cache."""
    now = datetime.utcnow()
    expired = [k for k, exp in _cache_expiry.items() if exp < now]
    for k in expired:
        _ai_cache.pop(k, None)
        _cache_expiry.pop(k, None)
    return len(expired)
