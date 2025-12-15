"""
Service Scoring - Calcul du FlipScore et recommandations
Version 2: Scoring hybride avec pondération discount + marge ajustée
"""

from typing import Optional, Dict, Any, Tuple, List
from loguru import logger


# Configuration des catégories
CATEGORY_WEIGHTS = {
    "sneakers_lifestyle": {
        "margin_threshold": 15,
        "liquidity_weight": 1.0,
        "popularity_weight": 1.0,
        "expected_sell_days": 7
    },
    "sneakers_running": {
        "margin_threshold": 12,
        "liquidity_weight": 0.8,
        "popularity_weight": 0.9,
        "expected_sell_days": 10
    },
    "streetwear": {
        "margin_threshold": 20,
        "liquidity_weight": 0.9,
        "popularity_weight": 1.1,
        "expected_sell_days": 5
    },
    "default": {
        "margin_threshold": 15,
        "liquidity_weight": 1.0,
        "popularity_weight": 1.0,
        "expected_sell_days": 7
    }
}

# Tiers de marques avec popularité
BRAND_TIERS = {
    "nike": {"tier": "S", "popularity_bonus": 1.2, "resale_factor": 1.1},
    "jordan": {"tier": "S", "popularity_bonus": 1.3, "resale_factor": 1.2},
    "adidas": {"tier": "A", "popularity_bonus": 1.1, "resale_factor": 1.05},
    "yeezy": {"tier": "S", "popularity_bonus": 1.25, "resale_factor": 1.15},
    "new balance": {"tier": "A", "popularity_bonus": 1.1, "resale_factor": 1.05},
    "asics": {"tier": "A", "popularity_bonus": 1.05, "resale_factor": 1.0},
    "salomon": {"tier": "A", "popularity_bonus": 1.1, "resale_factor": 1.05},
    "puma": {"tier": "B", "popularity_bonus": 0.95, "resale_factor": 0.95},
    "reebok": {"tier": "B", "popularity_bonus": 0.9, "resale_factor": 0.9},
    "converse": {"tier": "A", "popularity_bonus": 1.0, "resale_factor": 0.95},
    "vans": {"tier": "A", "popularity_bonus": 1.0, "resale_factor": 0.95},
    "the north face": {"tier": "A", "popularity_bonus": 1.05, "resale_factor": 1.0},
    "carhartt": {"tier": "A", "popularity_bonus": 1.05, "resale_factor": 1.0},
}


class ScoringEngine:
    """
    Moteur de scoring hybride v2
    
    FlipScore = Pondération de:
    - Discount % sur site source (35%)
    - Marge ajustée Vinted (25%)
    - Liquidité du marché (20%)
    - Popularité marque/modèle (15%)
    - Bonus contextuels (5%)
    """
    
    def _get_discount_score(self, discount_percent: float) -> float:
        """
        Score basé sur le % de réduction sur le site source.
        Une grosse réduction = potentiel de flip élevé.
        """
        if not discount_percent or discount_percent <= 0:
            return 20  # Pas de réduction connue = score neutre bas
        
        if discount_percent >= 60:
            return 100
        elif discount_percent >= 50:
            return 90
        elif discount_percent >= 40:
            return 75
        elif discount_percent >= 30:
            return 60
        elif discount_percent >= 20:
            return 45
        elif discount_percent >= 10:
            return 30
        else:
            return 20
    
    def _get_margin_score(self, margin_percent: float, margin_euro: float, source_type: str = "mixed") -> float:
        """
        Score basé sur la marge estimée (avec coefficient neuf appliqué).
        
        Args:
            margin_percent: Marge en %
            margin_euro: Marge en €
            source_type: "new" si basé sur prix neufs, "mixed" si ajusté
        """
        # Bonus si les prix viennent d'articles neufs (plus fiable)
        reliability_bonus = 10 if source_type == "new" else 0
        
        if margin_percent <= -50:
            base_score = 0
        elif margin_percent <= -20:
            base_score = 10
        elif margin_percent <= 0:
            base_score = 25 + (margin_percent + 20) * 0.75  # 25-40
        elif margin_percent < 10:
            base_score = 40 + margin_percent * 2  # 40-60
        elif margin_percent < 20:
            base_score = 60 + (margin_percent - 10) * 2  # 60-80
        elif margin_percent < 30:
            base_score = 80 + (margin_percent - 20)  # 80-90
        else:
            base_score = 90 + min((margin_percent - 30) / 2, 10)  # 90-100
        
        # Bonus marge € absolue
        if margin_euro >= 30:
            euro_bonus = 10
        elif margin_euro >= 20:
            euro_bonus = 7
        elif margin_euro >= 10:
            euro_bonus = 4
        elif margin_euro >= 0:
            euro_bonus = 2
        else:
            euro_bonus = 0
        
        return min(base_score + euro_bonus + reliability_bonus, 100)
    
    def _get_liquidity_score(self, nb_listings: int, liquidity_from_vinted: float, category: str = "default") -> float:
        """Score de liquidité = facilité de revente."""
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
        """Score de popularité de la marque."""
        cat_config = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])
        popularity_weight = cat_config["popularity_weight"]
        
        base_score = 50  # Score par défaut pour marques inconnues
        
        if brand:
            brand_lower = brand.lower()
            brand_info = BRAND_TIERS.get(brand_lower)
            
            if brand_info:
                tier = brand_info["tier"]
                bonus = brand_info["popularity_bonus"]
                
                if tier == "S":
                    base_score = 90
                elif tier == "A":
                    base_score = 75
                elif tier == "B":
                    base_score = 55
                else:
                    base_score = 40
                
                base_score *= bonus
        
        return min(base_score * popularity_weight, 100)
    
    def _get_contextual_bonus(
        self, 
        discount_percent: float, 
        sizes_available: Optional[List[str]], 
        color: Optional[str],
        source_type: str = "mixed"
    ) -> float:
        """Bonus contextuels."""
        bonus = 0
        
        # Grosse promo = urgence
        if discount_percent and discount_percent >= 50:
            bonus += 5
        
        # Tailles standards = revente facile
        standard_sizes = {"40", "41", "42", "43", "44", "45", "M", "L", "S", "XL"}
        if sizes_available:
            matching_sizes = set(str(s) for s in sizes_available) & standard_sizes
            if len(matching_sizes) >= 3:
                bonus += 5
            elif len(matching_sizes) >= 1:
                bonus += 2
        
        # Couleurs safe
        safe_colors = {"noir", "black", "blanc", "white", "gris", "grey", "gray", "beige", "cream"}
        if color and any(safe in color.lower() for safe in safe_colors):
            bonus += 3
        
        # Bonus fiabilité si prix neufs
        if source_type == "new":
            bonus += 2
        
        return bonus
    
    def calculate_flip_score(
        self,
        discount_percent: float,
        margin_percent: float,
        margin_euro: float,
        nb_listings: int,
        liquidity_score: float,
        brand: Optional[str] = None,
        category: str = "default",
        sizes_available: Optional[List[str]] = None,
        color: Optional[str] = None,
        source_type: str = "mixed"
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calcule le FlipScore avec la nouvelle pondération hybride.
        
        Pondération v2:
        - Discount: 35% (valorise les grosses promos)
        - Marge ajustée: 25%
        - Liquidité: 20%
        - Popularité: 15%
        - Contexte: 5%
        """
        discount_score = self._get_discount_score(discount_percent)
        margin_score = self._get_margin_score(margin_percent, margin_euro, source_type)
        liq_score = self._get_liquidity_score(nb_listings, liquidity_score, category)
        pop_score = self._get_popularity_score(brand, category)
        ctx_bonus = self._get_contextual_bonus(discount_percent, sizes_available, color, source_type)
        
        # Nouvelle pondération: Discount 35%, Marge 25%, Liquidité 20%, Popularité 15%, Contexte 5%
        weighted_score = (
            discount_score * 0.35 +
            margin_score * 0.25 +
            liq_score * 0.20 +
            pop_score * 0.15 +
            ctx_bonus
        )
        
        final_score = max(0, min(100, weighted_score))
        
        components = {
            "discount_score": round(discount_score, 1),
            "margin_score": round(margin_score, 1),
            "liquidity_score": round(liq_score, 1),
            "popularity_score": round(pop_score, 1),
            "contextual_bonus": round(ctx_bonus, 1)
        }
        
        return round(final_score, 1), components
    
    def get_recommendation(self, flip_score: float, margin_percent: float, margin_euro: float, discount_percent: float = 0) -> Tuple[str, float]:
        """
        Détermine la recommandation basée sur le score et les métriques.
        
        Logique v2:
        - BUY: Score élevé OU grosse promo avec bonne liquidité
        - WATCH: Score moyen ou potentiel
        - IGNORE: Score faible
        """
        # Cas spécial: grosse promo même si marge estimée négative
        if discount_percent >= 50 and flip_score >= 50:
            return "buy", min(0.85, flip_score / 100)
        
        if flip_score >= 65 and margin_percent >= 10:
            return "buy", min(0.90, flip_score / 100)
        elif flip_score >= 55 and (margin_percent >= 0 or discount_percent >= 40):
            return "buy", min(0.80, flip_score / 100)
        elif flip_score >= 45:
            return "watch", 0.55 + (flip_score - 45) / 100
        elif flip_score >= 35 or discount_percent >= 30:
            return "watch", 0.45
        else:
            return "ignore", 0.3 + flip_score / 200
    
    def calculate_recommended_price(self, vinted_stats: Dict[str, Any]) -> Dict[str, float]:
        """Calcule les prix de vente recommandés."""
        price_median = vinted_stats.get("price_median", 0)
        price_p25 = vinted_stats.get("price_p25", 0)
        price_p75 = vinted_stats.get("price_p75", 0)
        
        if not price_median:
            return {"aggressive": 0, "optimal": 0, "patient": 0}
        
        return {
            "aggressive": round(price_p25 * 0.95, 2),  # Vente rapide
            "optimal": round(price_median * 0.98, 2),   # Prix équilibré
            "patient": round(price_p75 * 0.95, 2)       # Maximiser profit
        }
    
    def estimate_sell_days(self, flip_score: float, liquidity_score: float, category: str = "default") -> int:
        """Estime le nombre de jours pour vendre."""
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
    
    def generate_explanation(
        self, 
        discount_percent: float,
        margin_percent: float, 
        nb_listings: int, 
        flip_score: float, 
        recommendation: str,
        source_type: str = "mixed"
    ) -> str:
        """Génère une explication lisible du score."""
        parts = []
        
        # Discount
        if discount_percent and discount_percent >= 40:
            parts.append(f"Promo exceptionnelle (-{discount_percent:.0f}%)")
        elif discount_percent and discount_percent >= 25:
            parts.append(f"Bonne promo (-{discount_percent:.0f}%)")
        
        # Marge
        if margin_percent >= 20:
            parts.append(f"marge excellente ({margin_percent:.0f}%)")
        elif margin_percent >= 10:
            parts.append(f"marge correcte ({margin_percent:.0f}%)")
        elif margin_percent >= 0:
            parts.append(f"marge faible ({margin_percent:.0f}%)")
        else:
            parts.append(f"marge estimée négative ({margin_percent:.0f}%)")
        
        # Liquidité
        if nb_listings >= 30:
            parts.append(f"marché très actif ({nb_listings} annonces)")
        elif nb_listings >= 10:
            parts.append(f"bonne liquidité ({nb_listings} annonces)")
        elif nb_listings >= 5:
            parts.append(f"liquidité moyenne ({nb_listings} annonces)")
        else:
            parts.append(f"peu d'annonces ({nb_listings})")
        
        # Source type
        if source_type == "new":
            parts.append("prix basés sur articles neufs")
        else:
            parts.append("estimation ajustée occasion→neuf")
        
        # Conclusion
        if recommendation == "buy":
            conclusion = "Deal recommandé à l'achat."
        elif recommendation == "watch":
            conclusion = "À surveiller."
        else:
            conclusion = "Pass recommandé."
        
        return f"{', '.join(parts)}. {conclusion}"
    
    def identify_risks(
        self, 
        nb_listings: int, 
        coefficient_variation: float, 
        margin_euro: float, 
        color: Optional[str],
        source_type: str = "mixed"
    ) -> List[str]:
        """Identifie les risques potentiels."""
        risks = []
        
        if nb_listings < 10:
            risks.append("Faible nombre d'annonces - revente potentiellement longue")
        
        if coefficient_variation and coefficient_variation > 30:
            risks.append("Prix très variables - estimation de marge incertaine")
        
        if margin_euro < 10:
            risks.append(f"Marge absolue faible ({margin_euro:.0f}€) - peu de marge d'erreur")
        
        if source_type == "mixed":
            risks.append("Prix estimés à partir d'articles occasion (coefficient appliqué)")
        
        risky_colors = ["rose", "pink", "jaune", "yellow", "orange", "violet", "purple", "fluo"]
        if color and any(c in color.lower() for c in risky_colors):
            risks.append("Coloris moins demandé - difficulté potentielle de revente")
        
        return risks


# Instance singleton
scoring_engine = ScoringEngine()


async def score_deal(deal_data: Dict[str, Any], vinted_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Fonction helper pour scorer un deal complet avec le nouveau système hybride."""
    
    margin_pct = vinted_stats.get("margin_pct", 0) or 0
    margin_euro = vinted_stats.get("margin_euro", 0) or 0
    nb_listings = vinted_stats.get("nb_listings", 0) or 0
    liquidity_score = vinted_stats.get("liquidity_score", 0) or 0
    source_type = vinted_stats.get("source_type", "mixed")
    
    category = deal_data.get("category", "default")
    discount_percent = deal_data.get("discount_percent", 0) or 0
    
    flip_score, components = scoring_engine.calculate_flip_score(
        discount_percent=discount_percent,
        margin_percent=margin_pct,
        margin_euro=margin_euro,
        nb_listings=nb_listings,
        liquidity_score=liquidity_score,
        brand=deal_data.get("brand"),
        category=category,
        sizes_available=deal_data.get("sizes_available"),
        color=deal_data.get("color"),
        source_type=source_type
    )
    
    recommendation, confidence = scoring_engine.get_recommendation(
        flip_score, margin_pct, margin_euro, discount_percent
    )
    recommended_prices = scoring_engine.calculate_recommended_price(vinted_stats)
    estimated_days = scoring_engine.estimate_sell_days(flip_score, liquidity_score, category)
    explanation = scoring_engine.generate_explanation(
        discount_percent, margin_pct, nb_listings, flip_score, recommendation, source_type
    )
    risks = scoring_engine.identify_risks(
        nb_listings, 
        vinted_stats.get("coefficient_variation", 0), 
        margin_euro, 
        deal_data.get("color"),
        source_type
    )
    
    return {
        "flip_score": flip_score,
        "discount_score": components["discount_score"],
        "margin_score": components["margin_score"],
        "liquidity_score": components["liquidity_score"],
        "popularity_score": components["popularity_score"],
        "recommended_action": recommendation,
        "recommended_price": recommended_prices["optimal"],
        "recommended_price_range": recommended_prices,
        "confidence": confidence,
        "explanation": explanation,
        "explanation_short": explanation[:250] if len(explanation) > 250 else explanation,
        "risks": risks,
        "estimated_sell_days": estimated_days,
        "model_version": "hybrid_v2",
        "score_breakdown": components,
        "vinted_source_type": source_type,
    }
