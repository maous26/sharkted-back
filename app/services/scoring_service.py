"""
Service Scoring - Calcul du FlipScore AUTONOME (sans Vinted)
Version 3: Scoring basé sur discount, marque, catégorie uniquement
PAS DE PROXY RÉSIDENTIEL - PAS DE VINTED
"""

from typing import Optional, Dict, Any, Tuple, List
from loguru import logger


# =============================================================================
# CONFIGURATION DES MARQUES (données de revente connues)
# =============================================================================

BRAND_RESALE_DATA = {
    # Tier S - Excellente revente, forte demande
    "nike": {"tier": "S", "resale_multiplier": 1.15, "demand_score": 95, "avg_days_to_sell": 5},
    "jordan": {"tier": "S", "resale_multiplier": 1.25, "demand_score": 98, "avg_days_to_sell": 3},
    "yeezy": {"tier": "S", "resale_multiplier": 1.20, "demand_score": 90, "avg_days_to_sell": 7},
    "dunk": {"tier": "S", "resale_multiplier": 1.18, "demand_score": 92, "avg_days_to_sell": 4},
    
    # Tier A - Bonne revente
    "adidas": {"tier": "A", "resale_multiplier": 1.08, "demand_score": 80, "avg_days_to_sell": 10},
    "new balance": {"tier": "A", "resale_multiplier": 1.10, "demand_score": 82, "avg_days_to_sell": 8},
    "asics": {"tier": "A", "resale_multiplier": 1.05, "demand_score": 75, "avg_days_to_sell": 12},
    "salomon": {"tier": "A", "resale_multiplier": 1.08, "demand_score": 78, "avg_days_to_sell": 10},
    "converse": {"tier": "A", "resale_multiplier": 1.0, "demand_score": 70, "avg_days_to_sell": 14},
    "vans": {"tier": "A", "resale_multiplier": 0.98, "demand_score": 68, "avg_days_to_sell": 14},
    "the north face": {"tier": "A", "resale_multiplier": 1.05, "demand_score": 75, "avg_days_to_sell": 12},
    "carhartt": {"tier": "A", "resale_multiplier": 1.02, "demand_score": 72, "avg_days_to_sell": 14},
    
    # Tier B - Revente moyenne
    "puma": {"tier": "B", "resale_multiplier": 0.92, "demand_score": 55, "avg_days_to_sell": 20},
    "reebok": {"tier": "B", "resale_multiplier": 0.88, "demand_score": 50, "avg_days_to_sell": 25},
    "fila": {"tier": "B", "resale_multiplier": 0.85, "demand_score": 45, "avg_days_to_sell": 30},
    "under armour": {"tier": "B", "resale_multiplier": 0.90, "demand_score": 52, "avg_days_to_sell": 22},
    "lacoste": {"tier": "B", "resale_multiplier": 0.95, "demand_score": 60, "avg_days_to_sell": 18},
    
    # Tier C - Revente difficile
    "kappa": {"tier": "C", "resale_multiplier": 0.75, "demand_score": 35, "avg_days_to_sell": 40},
    "le coq sportif": {"tier": "C", "resale_multiplier": 0.78, "demand_score": 38, "avg_days_to_sell": 35},
    "diadora": {"tier": "C", "resale_multiplier": 0.80, "demand_score": 40, "avg_days_to_sell": 30},
}

# Catégories avec ajustements
CATEGORY_CONFIG = {
    "sneakers": {"demand_multiplier": 1.15, "margin_threshold": 25},
    "sneakers_lifestyle": {"demand_multiplier": 1.10, "margin_threshold": 25},
    "sneakers_running": {"demand_multiplier": 0.95, "margin_threshold": 20},
    "streetwear": {"demand_multiplier": 1.05, "margin_threshold": 30},
    "clothing": {"demand_multiplier": 0.90, "margin_threshold": 35},
    "accessories": {"demand_multiplier": 0.85, "margin_threshold": 40},
    "default": {"demand_multiplier": 1.0, "margin_threshold": 25},
}

# Tailles standard (faciles à revendre)
STANDARD_SIZES = {
    "shoes": {"39", "40", "41", "42", "43", "44", "45"},
    "clothing": {"S", "M", "L", "XL"},
}

# Couleurs safe vs risquées
SAFE_COLORS = {"black", "noir", "white", "blanc", "grey", "gris", "navy", "blue", "bleu"}
RISKY_COLORS = {"pink", "rose", "yellow", "jaune", "orange", "violet", "purple", "fluo", "neon"}


class ScoringEngineV3:
    """
    Moteur de scoring AUTONOME v3 - SANS VINTED
    
    FlipScore = Pondération de:
    - Discount % (40%) - La réduction sur le prix original
    - Brand Score (30%) - Popularité et facilité de revente de la marque
    - Contextual Score (30%) - Tailles, couleurs, catégorie
    
    Marge estimée = (prix_original * resale_multiplier) - prix_soldé
    """
    
    def _get_brand_info(self, brand: Optional[str]) -> Dict[str, Any]:
        """Récupère les infos de revente de la marque."""
        if not brand:
            return {"tier": "C", "resale_multiplier": 0.85, "demand_score": 40, "avg_days_to_sell": 30}
        
        brand_lower = brand.lower().strip()
        
        # Cherche correspondance exacte ou partielle
        for key, data in BRAND_RESALE_DATA.items():
            if key in brand_lower or brand_lower in key:
                return data
        
        # Marque inconnue = tier B par défaut
        return {"tier": "B", "resale_multiplier": 0.90, "demand_score": 50, "avg_days_to_sell": 20}
    
    def _get_discount_score(self, discount_percent: float) -> float:
        """
        Score basé sur le % de réduction.
        Plus la réduction est grande, plus le potentiel est élevé.
        """
        if not discount_percent or discount_percent <= 0:
            return 15
        
        if discount_percent >= 70:
            return 100
        elif discount_percent >= 60:
            return 95
        elif discount_percent >= 50:
            return 85
        elif discount_percent >= 40:
            return 70
        elif discount_percent >= 30:
            return 55
        elif discount_percent >= 20:
            return 40
        elif discount_percent >= 10:
            return 25
        else:
            return 15
    
    def _get_brand_score(self, brand: Optional[str], category: str = "default") -> float:
        """Score basé sur la marque et sa facilité de revente."""
        brand_info = self._get_brand_info(brand)
        cat_config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["default"])
        
        # Score de base selon le tier
        tier_scores = {"S": 95, "A": 75, "B": 50, "C": 30}
        base_score = tier_scores.get(brand_info["tier"], 40)
        
        # Ajustement selon la demande
        demand_bonus = (brand_info["demand_score"] - 50) / 5  # -10 à +10
        
        # Ajustement catégorie
        category_multiplier = cat_config["demand_multiplier"]
        
        final_score = (base_score + demand_bonus) * category_multiplier
        return min(max(final_score, 0), 100)
    
    def _get_contextual_score(
        self,
        sizes_available: Optional[List[str]],
        color: Optional[str],
        discount_percent: float,
        category: str = "default"
    ) -> float:
        """Score contextuel basé sur tailles, couleurs, etc."""
        score = 50  # Score de base neutre
        
        # Bonus/malus tailles
        if sizes_available:
            sizes_set = set(str(s).upper() for s in sizes_available)
            standard = STANDARD_SIZES.get("shoes", set()) | STANDARD_SIZES.get("clothing", set())
            matching = sizes_set & standard
            
            if len(matching) >= 4:
                score += 25  # Excellente disponibilité
            elif len(matching) >= 2:
                score += 15
            elif len(matching) >= 1:
                score += 5
            else:
                score -= 10  # Tailles atypiques
        
        # Bonus/malus couleurs
        if color:
            color_lower = color.lower()
            if any(safe in color_lower for safe in SAFE_COLORS):
                score += 15  # Couleur safe
            elif any(risky in color_lower for risky in RISKY_COLORS):
                score -= 15  # Couleur risquée
        
        # Bonus grosse promo (urgence)
        if discount_percent >= 60:
            score += 10
        elif discount_percent >= 50:
            score += 5
        
        return min(max(score, 0), 100)
    
    def estimate_resale_price(
        self,
        original_price: float,
        sale_price: float,
        brand: Optional[str]
    ) -> Dict[str, float]:
        """
        Estime le prix de revente sans Vinted.
        Basé sur le prix original et le multiplicateur de la marque.
        """
        brand_info = self._get_brand_info(brand)
        multiplier = brand_info["resale_multiplier"]
        
        # Le prix de revente estimé est basé sur le prix original
        # (ce que les gens sont prêts à payer pour du neuf)
        estimated_resale = original_price * multiplier
        
        # Différentes stratégies de prix
        return {
            "aggressive": round(estimated_resale * 0.85, 2),  # Vente rapide -15%
            "optimal": round(estimated_resale * 0.92, 2),     # Prix équilibré -8%
            "patient": round(estimated_resale * 0.98, 2),     # Prix max -2%
            "estimated_resale": round(estimated_resale, 2),
        }
    
    def calculate_estimated_margin(
        self,
        original_price: float,
        sale_price: float,
        brand: Optional[str]
    ) -> Tuple[float, float]:
        """
        Calcule la marge estimée sans données Vinted.
        
        Returns:
            (margin_euro, margin_percent)
        """
        prices = self.estimate_resale_price(original_price, sale_price, brand)
        estimated_resale = prices["optimal"]  # Prix de vente réaliste
        
        # Frais estimés (Vinted: ~13% = 5% commission + 3% paiement + frais fixes)
        platform_fees = estimated_resale * 0.13
        shipping_estimate = 5.0  # Frais d'envoi moyens
        
        net_after_fees = estimated_resale - platform_fees - shipping_estimate
        margin_euro = net_after_fees - sale_price
        margin_percent = (margin_euro / sale_price * 100) if sale_price > 0 else 0
        
        return round(margin_euro, 2), round(margin_percent, 1)
    
    def calculate_flip_score(
        self,
        original_price: float,
        sale_price: float,
        discount_percent: float,
        brand: Optional[str] = None,
        category: str = "default",
        sizes_available: Optional[List[str]] = None,
        color: Optional[str] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calcule le FlipScore AUTONOME (sans Vinted).
        
        Pondération:
        - Discount: 40%
        - Brand: 30%
        - Contextual: 30%
        """
        # Calculs des composants
        discount_score = self._get_discount_score(discount_percent)
        brand_score = self._get_brand_score(brand, category)
        contextual_score = self._get_contextual_score(sizes_available, color, discount_percent, category)
        
        # Marge estimée
        margin_euro, margin_pct = self.calculate_estimated_margin(original_price, sale_price, brand)
        
        # Bonus/malus marge estimée
        margin_bonus = 0
        if margin_pct >= 30:
            margin_bonus = 10
        elif margin_pct >= 20:
            margin_bonus = 5
        elif margin_pct < 0:
            margin_bonus = -10
        
        # Score final pondéré
        flip_score = (
            discount_score * 0.40 +
            brand_score * 0.30 +
            contextual_score * 0.30 +
            margin_bonus
        )
        
        flip_score = min(max(flip_score, 0), 100)
        
        components = {
            "discount_score": round(discount_score, 1),
            "brand_score": round(brand_score, 1),
            "contextual_score": round(contextual_score, 1),
            "margin_bonus": margin_bonus,
            "estimated_margin_euro": margin_euro,
            "estimated_margin_pct": margin_pct,
        }
        
        return round(flip_score, 1), components
    
    def get_recommendation(
        self,
        flip_score: float,
        margin_euro: float,
        margin_pct: float,
        discount_percent: float = 0
    ) -> Tuple[str, float]:
        """Détermine la recommandation."""
        
        # Grosse promo = toujours intéressant si score OK
        if discount_percent >= 50 and flip_score >= 55:
            return "buy", min(0.85, flip_score / 100)
        
        if flip_score >= 70 and margin_pct >= 15:
            return "buy", min(0.92, flip_score / 100)
        elif flip_score >= 60 and margin_pct >= 10:
            return "buy", min(0.80, flip_score / 100)
        elif flip_score >= 50 or discount_percent >= 40:
            return "watch", 0.55 + (flip_score - 50) / 100
        elif flip_score >= 40:
            return "watch", 0.45
        else:
            return "ignore", 0.3
    
    def estimate_sell_days(self, flip_score: float, brand: Optional[str]) -> int:
        """Estime le nombre de jours pour vendre."""
        brand_info = self._get_brand_info(brand)
        base_days = brand_info["avg_days_to_sell"]
        
        if flip_score >= 80:
            return max(2, base_days - 3)
        elif flip_score >= 70:
            return base_days
        elif flip_score >= 60:
            return base_days + 5
        else:
            return base_days + 10
    
    def generate_explanation(
        self,
        discount_percent: float,
        margin_euro: float,
        margin_pct: float,
        flip_score: float,
        recommendation: str,
        brand: Optional[str]
    ) -> str:
        """Génère une explication du score."""
        parts = []
        brand_info = self._get_brand_info(brand)
        
        # Discount
        if discount_percent >= 50:
            parts.append(f"Promo exceptionnelle (-{discount_percent:.0f}%)")
        elif discount_percent >= 30:
            parts.append(f"Bonne promo (-{discount_percent:.0f}%)")
        elif discount_percent >= 15:
            parts.append(f"Promo modérée (-{discount_percent:.0f}%)")
        
        # Marque
        tier = brand_info["tier"]
        if tier == "S":
            parts.append("marque très recherchée")
        elif tier == "A":
            parts.append("marque populaire")
        elif tier == "B":
            parts.append("marque correcte")
        else:
            parts.append("marque peu demandée")
        
        # Marge estimée
        if margin_pct >= 25:
            parts.append(f"marge estimée excellente (~{margin_pct:.0f}%)")
        elif margin_pct >= 15:
            parts.append(f"marge estimée correcte (~{margin_pct:.0f}%)")
        elif margin_pct >= 5:
            parts.append(f"marge estimée faible (~{margin_pct:.0f}%)")
        else:
            parts.append(f"marge estimée très faible (~{margin_pct:.0f}%)")
        
        # Conclusion
        if recommendation == "buy":
            conclusion = "Deal recommandé."
        elif recommendation == "watch":
            conclusion = "À surveiller."
        else:
            conclusion = "Pass recommandé."
        
        return f"{', '.join(parts)}. {conclusion}"
    
    def identify_risks(
        self,
        margin_euro: float,
        margin_pct: float,
        color: Optional[str],
        brand: Optional[str],
        sizes_available: Optional[List[str]]
    ) -> List[str]:
        """Identifie les risques."""
        risks = []
        brand_info = self._get_brand_info(brand)
        
        if margin_euro < 10:
            risks.append(f"Marge estimée faible ({margin_euro:.0f}€)")
        
        if margin_pct < 10:
            risks.append("Potentiel de profit limité")
        
        if brand_info["tier"] in ["C"]:
            risks.append("Marque peu recherchée - revente potentiellement longue")
        
        if color and any(c in color.lower() for c in RISKY_COLORS):
            risks.append("Coloris atypique - difficulté de revente possible")
        
        if sizes_available:
            sizes_set = set(str(s).upper() for s in sizes_available)
            standard = STANDARD_SIZES.get("shoes", set()) | STANDARD_SIZES.get("clothing", set())
            if not (sizes_set & standard):
                risks.append("Aucune taille standard disponible")
        
        return risks


# Instance singleton
scoring_engine = ScoringEngineV3()


async def score_deal(deal_data: Dict[str, Any], vinted_stats: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Fonction helper pour scorer un deal avec le système AUTONOME v3.
    vinted_stats est ignoré - le scoring est basé uniquement sur les données du deal.
    """
    
    original_price = deal_data.get("original_price", 0) or deal_data.get("price", 0)
    sale_price = deal_data.get("sale_price", 0) or deal_data.get("price", 0)
    discount_percent = deal_data.get("discount_percent", 0) or 0
    brand = deal_data.get("brand")
    category = deal_data.get("category", "default")
    sizes_available = deal_data.get("sizes_available")
    color = deal_data.get("color")
    
    # Calcul du score autonome
    flip_score, components = scoring_engine.calculate_flip_score(
        original_price=original_price,
        sale_price=sale_price,
        discount_percent=discount_percent,
        brand=brand,
        category=category,
        sizes_available=sizes_available,
        color=color,
    )
    
    margin_euro = components["estimated_margin_euro"]
    margin_pct = components["estimated_margin_pct"]
    
    recommendation, confidence = scoring_engine.get_recommendation(
        flip_score, margin_euro, margin_pct, discount_percent
    )
    
    recommended_prices = scoring_engine.estimate_resale_price(original_price, sale_price, brand)
    estimated_days = scoring_engine.estimate_sell_days(flip_score, brand)
    
    explanation = scoring_engine.generate_explanation(
        discount_percent, margin_euro, margin_pct, flip_score, recommendation, brand
    )
    
    risks = scoring_engine.identify_risks(
        margin_euro, margin_pct, color, brand, sizes_available
    )
    
    return {
        "flip_score": flip_score,
        "discount_score": components["discount_score"],
        "brand_score": components["brand_score"],
        "contextual_score": components["contextual_score"],
        "margin_score": 0,  # Pas de score marge Vinted
        "liquidity_score": 0,  # Pas de liquidité Vinted
        "popularity_score": components["brand_score"],  # Utilise brand_score
        "recommended_action": recommendation,
        "recommended_price": recommended_prices["optimal"],
        "recommended_price_range": recommended_prices,
        "confidence": confidence,
        "explanation": explanation,
        "explanation_short": explanation[:200] if len(explanation) > 200 else explanation,
        "risks": risks,
        "estimated_sell_days": estimated_days,
        "model_version": "autonomous_v3",
        "score_breakdown": components,
        "vinted_source_type": "none",  # Plus de Vinted
        # Marges estimées (sans Vinted)
        "margin_euro": margin_euro,
        "margin_pct": margin_pct,
    }


# =============================================================================
# INTÉGRATION AI ENHANCER
# =============================================================================

from app.services.ai_scoring_enhancer import enhance_flip_score, quick_analysis


async def score_deal_with_ai(deal_data: Dict[str, Any], use_ai: bool = True) -> Dict[str, Any]:
    """
    Score un deal avec enrichissement IA.
    Combine le scoring autonome v3 + analyse IA pour plus de fiabilité.
    """
    # 1. Score autonome de base
    base_result = await score_deal(deal_data, None)
    base_score = base_result["flip_score"]
    
    # 2. Enrichissement IA
    original_price = deal_data.get("original_price", 0) or deal_data.get("price", 0)
    sale_price = deal_data.get("sale_price", 0) or deal_data.get("price", 0)
    discount_pct = deal_data.get("discount_percent", 0) or 0
    
    enhanced_score, ai_analysis = await enhance_flip_score(
        base_score=base_score,
        product_name=deal_data.get("title", deal_data.get("product_name", "")),
        brand=deal_data.get("brand", ""),
        original_price=original_price,
        sale_price=sale_price,
        discount_pct=discount_pct,
        use_ai=use_ai,
    )
    
    # 3. Fusionner les résultats
    result = base_result.copy()
    result["flip_score"] = enhanced_score
    result["base_score"] = base_score
    result["ai_adjustment"] = ai_analysis.get("adjustment", 0)
    result["ai_analysis"] = ai_analysis.get("analysis", {})
    result["ai_method"] = ai_analysis.get("method", "none")
    result["model_version"] = "autonomous_v3_ai"
    
    # Mettre à jour la recommandation si le score a changé significativement
    if abs(enhanced_score - base_score) >= 10:
        margin_pct = result.get("margin_pct", 0)
        margin_euro = result.get("margin_euro", 0)
        recommendation, confidence = scoring_engine.get_recommendation(
            enhanced_score, margin_euro, margin_pct, discount_pct
        )
        result["recommended_action"] = recommendation
        result["confidence"] = confidence
    
    # Ajouter l'explication IA
    if ai_analysis.get("analysis", {}).get("reasoning"):
        result["ai_reasoning"] = ai_analysis["analysis"]["reasoning"]
    
    return result
