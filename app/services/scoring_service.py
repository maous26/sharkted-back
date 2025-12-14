"""
Service Scoring - Calcul du FlipScore et recommandations
Version 1: Règles pondérées (MVP)
"""

from typing import Optional, Dict, Any, Tuple, List
from loguru import logger


# Configuration des catégories
CATEGORY_WEIGHTS = {
    "sneakers_lifestyle": {
        "margin_threshold": 25,
        "liquidity_weight": 1.0,
        "popularity_weight": 1.0,
        "expected_sell_days": 7
    },
    "sneakers_running": {
        "margin_threshold": 20,
        "liquidity_weight": 0.8,
        "popularity_weight": 0.9,
        "expected_sell_days": 10
    },
    "streetwear": {
        "margin_threshold": 30,
        "liquidity_weight": 0.9,
        "popularity_weight": 1.1,
        "expected_sell_days": 5
    },
    "default": {
        "margin_threshold": 25,
        "liquidity_weight": 1.0,
        "popularity_weight": 1.0,
        "expected_sell_days": 7
    }
}

# Tiers de marques
BRAND_TIERS = {
    "nike": {"tier": "S", "popularity_bonus": 1.2},
    "jordan": {"tier": "S", "popularity_bonus": 1.3},
    "adidas": {"tier": "A", "popularity_bonus": 1.1},
    "yeezy": {"tier": "S", "popularity_bonus": 1.25},
    "new balance": {"tier": "A", "popularity_bonus": 1.05},
    "asics": {"tier": "B", "popularity_bonus": 1.0},
    "puma": {"tier": "B", "popularity_bonus": 0.95},
    "reebok": {"tier": "B", "popularity_bonus": 0.9},
    "converse": {"tier": "A", "popularity_bonus": 1.0},
    "vans": {"tier": "A", "popularity_bonus": 1.0},
}


class ScoringEngine:
    """
    Moteur de scoring pour évaluer la qualité des deals
    
    FlipScore = Pondération de:
    - Marge potentielle (40%)
    - Liquidité du marché (30%)
    - Popularité marque/modèle (20%)
    - Bonus/Malus contextuels (10%)
    """
    
    def _get_margin_score(self, margin_percent: float, margin_euro: float, category: str = "default") -> float:
        cat_config = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        threshold = cat_config["margin_threshold"]
        
        if margin_percent <= 0:
            base_score = 0
        elif margin_percent < threshold:
            base_score = (margin_percent / threshold) * 50
        elif margin_percent < threshold * 2:
            base_score = 50 + ((margin_percent - threshold) / threshold) * 30
        else:
            base_score = 80 + min((margin_percent - threshold * 2) / 20, 20)
        
        # Bonus marge € absolue
        if margin_euro >= 50:
            euro_bonus = 10
        elif margin_euro >= 30:
            euro_bonus = 5
        elif margin_euro >= 20:
            euro_bonus = 2
        else:
            euro_bonus = 0
        
        return min(base_score + euro_bonus, 100)
    
    def _get_liquidity_score(self, nb_listings: int, liquidity_from_vinted: float, category: str = "default") -> float:
        cat_config = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        liquidity_weight = cat_config["liquidity_weight"]
        
        if nb_listings == 0:
            listings_score = 0
        elif nb_listings < 5:
            listings_score = 20
        elif nb_listings < 15:
            listings_score = 40
        elif nb_listings < 30:
            listings_score = 60
        elif nb_listings < 50:
            listings_score = 80
        else:
            listings_score = 100
        
        combined_score = (listings_score * 0.4 + liquidity_from_vinted * 0.6)
        return combined_score * liquidity_weight
    
    def _get_popularity_score(self, brand: Optional[str], category: str = "default") -> float:
        cat_config = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        popularity_weight = cat_config["popularity_weight"]
        
        base_score = 50
        
        if brand:
            brand_lower = brand.lower()
            brand_info = BRAND_TIERS.get(brand_lower)
            
            if brand_info:
                tier = brand_info["tier"]
                bonus = brand_info["popularity_bonus"]
                
                if tier == "S":
                    base_score = 85
                elif tier == "A":
                    base_score = 70
                elif tier == "B":
                    base_score = 55
                else:
                    base_score = 40
                
                base_score *= bonus
        
        return min(base_score * popularity_weight, 100)
    
    def _get_contextual_bonus(self, discount_percent: float, sizes_available: Optional[List[str]], color: Optional[str]) -> float:
        bonus = 0
        
        if discount_percent >= 70:
            bonus += 10
        elif discount_percent >= 50:
            bonus += 5
        
        standard_sizes = {"40", "41", "42", "43", "44", "M", "L", "S"}
        if sizes_available:
            matching_sizes = set(str(s) for s in sizes_available) & standard_sizes
            if len(matching_sizes) >= 3:
                bonus += 5
            elif len(matching_sizes) >= 1:
                bonus += 2
        
        safe_colors = {"noir", "black", "blanc", "white", "gris", "grey", "gray"}
        if color and any(safe in color.lower() for safe in safe_colors):
            bonus += 3
        
        return bonus
    
    def calculate_flip_score(
        self,
        margin_percent: float,
        margin_euro: float,
        nb_listings: int,
        liquidity_score: float,
        brand: Optional[str] = None,
        category: str = "default",
        discount_percent: float = 0,
        sizes_available: Optional[List[str]] = None,
        color: Optional[str] = None
    ) -> Tuple[float, Dict[str, float]]:
        
        margin_score = self._get_margin_score(margin_percent, margin_euro, category)
        liq_score = self._get_liquidity_score(nb_listings, liquidity_score, category)
        pop_score = self._get_popularity_score(brand, category)
        ctx_bonus = self._get_contextual_bonus(discount_percent, sizes_available, color)
        
        # Pondération: Marge 40%, Liquidité 30%, Popularité 20%, Contexte 10%
        weighted_score = (
            margin_score * 0.40 +
            liq_score * 0.30 +
            pop_score * 0.20 +
            ctx_bonus
        )
        
        final_score = max(0, min(100, weighted_score))
        
        components = {
            "margin_score": round(margin_score, 1),
            "liquidity_score": round(liq_score, 1),
            "popularity_score": round(pop_score, 1),
            "contextual_bonus": round(ctx_bonus, 1)
        }
        
        return round(final_score, 1), components
    
    def get_recommendation(self, flip_score: float, margin_percent: float, margin_euro: float) -> Tuple[str, float]:
        if flip_score >= 80 and margin_percent >= 30 and margin_euro >= 20:
            return "buy", min(0.95, flip_score / 100)
        elif flip_score >= 70 and margin_percent >= 25:
            return "buy", min(0.85, flip_score / 100)
        elif flip_score >= 60 and margin_percent >= 20:
            return "watch", 0.6 + (flip_score - 60) / 100
        elif flip_score >= 50:
            return "watch", 0.5
        else:
            return "ignore", 0.3 + flip_score / 200
    
    def calculate_recommended_price(self, vinted_stats: Dict[str, Any]) -> Dict[str, float]:
        price_median = vinted_stats.get("price_median", 0)
        price_p25 = vinted_stats.get("price_p25", 0)
        price_p75 = vinted_stats.get("price_p75", 0)
        
        if not price_median:
            return {"aggressive": 0, "optimal": 0, "patient": 0}
        
        return {
            "aggressive": round(price_p25 * 0.95, 2),
            "optimal": round(price_median * 0.98, 2),
            "patient": round(price_p75 * 0.95, 2)
        }
    
    def estimate_sell_days(self, flip_score: float, liquidity_score: float, category: str = "default") -> int:
        cat_config = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        base_days = cat_config["expected_sell_days"]
        
        if flip_score >= 80 and liquidity_score >= 70:
            return max(3, base_days - 4)
        elif flip_score >= 70:
            return base_days
        elif flip_score >= 60:
            return base_days + 3
        else:
            return base_days + 7
    
    def generate_explanation(self, margin_percent: float, nb_listings: int, flip_score: float, recommendation: str) -> str:
        parts = []
        
        if margin_percent >= 40:
            parts.append(f"Excellente marge ({margin_percent:.0f}%)")
        elif margin_percent >= 25:
            parts.append(f"Bonne marge ({margin_percent:.0f}%)")
        elif margin_percent >= 15:
            parts.append(f"Marge correcte ({margin_percent:.0f}%)")
        else:
            parts.append(f"Marge faible ({margin_percent:.0f}%)")
        
        if nb_listings >= 30:
            parts.append(f"marché très actif ({nb_listings} annonces)")
        elif nb_listings >= 10:
            parts.append(f"marché actif ({nb_listings} annonces)")
        elif nb_listings >= 5:
            parts.append(f"liquidité moyenne ({nb_listings} annonces)")
        else:
            parts.append(f"peu d'annonces ({nb_listings})")
        
        if recommendation == "buy":
            conclusion = "Deal recommandé à l'achat."
        elif recommendation == "watch":
            conclusion = "À surveiller pour une meilleure opportunité."
        else:
            conclusion = "Pass recommandé."
        
        return f"{', '.join(parts)}. {conclusion}"
    
    def identify_risks(self, nb_listings: int, coefficient_variation: float, margin_euro: float, color: Optional[str]) -> List[str]:
        risks = []
        
        if nb_listings < 10:
            risks.append("Faible nombre d'annonces - revente potentiellement longue")
        
        if coefficient_variation and coefficient_variation > 30:
            risks.append("Prix très variables - estimation de marge incertaine")
        
        if margin_euro < 15:
            risks.append(f"Marge absolue faible ({margin_euro:.0f}€) - peu de marge d'erreur")
        
        risky_colors = ["rose", "pink", "jaune", "yellow", "orange", "violet", "purple"]
        if color and any(c in color.lower() for c in risky_colors):
            risks.append("Coloris moins demandé - difficulté potentielle de revente")
        
        return risks


# Instance singleton
scoring_engine = ScoringEngine()


async def score_deal(deal_data: Dict[str, Any], vinted_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Fonction helper pour scorer un deal complet."""
    
    margin_pct = vinted_stats.get("margin_pct", 0) or 0
    margin_euro = vinted_stats.get("margin_euro", 0) or 0
    nb_listings = vinted_stats.get("nb_listings", 0) or 0
    liquidity_score = vinted_stats.get("liquidity_score", 0) or 0
    
    category = deal_data.get("category", "default")
    
    flip_score, components = scoring_engine.calculate_flip_score(
        margin_percent=margin_pct,
        margin_euro=margin_euro,
        nb_listings=nb_listings,
        liquidity_score=liquidity_score,
        brand=deal_data.get("brand"),
        category=category,
        discount_percent=deal_data.get("discount_percent", 0) or 0,
        sizes_available=deal_data.get("sizes_available"),
        color=deal_data.get("color")
    )
    
    recommendation, confidence = scoring_engine.get_recommendation(flip_score, margin_pct, margin_euro)
    recommended_prices = scoring_engine.calculate_recommended_price(vinted_stats)
    estimated_days = scoring_engine.estimate_sell_days(flip_score, liquidity_score, category)
    explanation = scoring_engine.generate_explanation(margin_pct, nb_listings, flip_score, recommendation)
    risks = scoring_engine.identify_risks(nb_listings, vinted_stats.get("coefficient_variation", 0), margin_euro, deal_data.get("color"))
    
    return {
        "flip_score": flip_score,
        "popularity_score": components["popularity_score"],
        "liquidity_score": components["liquidity_score"],
        "margin_score": components["margin_score"],
        "recommended_action": recommendation,
        "recommended_price": recommended_prices["optimal"],
        "recommended_price_range": recommended_prices,
        "confidence": confidence,
        "explanation": explanation,
        "explanation_short": explanation[:250] if len(explanation) > 250 else explanation,
        "risks": risks,
        "estimated_sell_days": estimated_days,
        "model_version": "rules_v1",
        "score_breakdown": components
    }
