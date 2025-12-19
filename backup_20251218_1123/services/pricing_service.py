"""
Service de Pricing Intelligent
Calcule les prix de vente optimaux basés sur les données Vinted et le contexte
"""

from typing import Dict, Any, Optional
from loguru import logger

# Configuration des frais par plateforme
PLATFORM_FEES = {
    "vinted": {"commission_pct": 0, "protection_pct": 5, "shipping_avg": 4.50},
    "leboncoin": {"commission_pct": 0, "protection_pct": 4, "shipping_avg": 5.0}
}

BRAND_DEMAND_FACTOR = {
    "nike": 1.05, "jordan": 1.10, "adidas": 1.02, "yeezy": 1.15,
    "new balance": 1.03, "asics": 1.0, "salomon": 1.05, "puma": 0.95,
    "reebok": 0.92, "converse": 0.98, "vans": 0.97,
}

SEASONALITY = {
    1: -5, 2: -3, 3: 0, 4: 2, 5: 3, 6: 0,
    7: -5, 8: -3, 9: 5, 10: 3, 11: 8, 12: 5,
}


class PricingEngine:
    def calculate_smart_price(
        self,
        buy_price: float,
        vinted_stats: Dict[str, Any],
        brand: Optional[str] = None,
        condition: str = "new_with_tags",
        target_margin_pct: float = 15.0,
        urgency: str = "normal"
    ) -> Dict[str, Any]:
        nb_listings = vinted_stats.get("nb_listings", 0)
        
        if nb_listings == 0:
            return self._no_data_fallback(buy_price, target_margin_pct)
        
        price_p25 = vinted_stats.get("price_p25") or vinted_stats.get("price_median", 0) * 0.85
        price_median = vinted_stats.get("price_median", 0)
        price_p75 = vinted_stats.get("price_p75") or price_median * 1.15
        coef_var = vinted_stats.get("coefficient_variation", 0.3)
        
        brand_factor = self._get_brand_factor(brand)
        condition_factor = self._get_condition_factor(condition)
        season_factor = self._get_season_factor()
        liquidity_factor = self._get_liquidity_factor(nb_listings, vinted_stats.get("liquidity_score", 50))
        
        base_price = price_median * brand_factor * condition_factor * season_factor
        
        strategies = {
            "fast": {"price": price_p25 * 0.95 * condition_factor, "days": max(2, 7 - int(liquidity_factor * 3)), "description": "Vente rapide"},
            "normal": {"price": base_price * 0.98, "days": max(5, 10 - int(liquidity_factor * 2)), "description": "Prix équilibré"},
            "patient": {"price": min(price_p75, base_price * 1.05) * condition_factor, "days": max(10, 21 - int(liquidity_factor * 5)), "description": "Maximiser profit"}
        }
        
        selected = strategies.get(urgency, strategies["normal"])
        recommended_price = round(selected["price"], 2)
        
        margin_euro = recommended_price - buy_price
        margin_pct = (margin_euro / buy_price * 100) if buy_price > 0 else 0
        
        if margin_pct < 0 and urgency != "fast":
            min_viable_price = buy_price * (1 + target_margin_pct / 100)
            if min_viable_price <= price_p75:
                recommended_price = round(min_viable_price, 2)
                margin_euro = recommended_price - buy_price
                margin_pct = target_margin_pct
        
        confidence = self._calculate_confidence(nb_listings, coef_var, vinted_stats.get("source_type", "mixed"))
        
        return {
            "recommended_price": recommended_price,
            "price_range": {"min": round(strategies["fast"]["price"], 2), "optimal": round(strategies["normal"]["price"], 2), "max": round(strategies["patient"]["price"], 2)},
            "expected_margin": {"euro": round(margin_euro, 2), "pct": round(margin_pct, 1)},
            "expected_sell_days": selected["days"],
            "confidence": round(confidence, 2),
            "strategy": urgency,
            "strategy_description": selected["description"],
            "breakdown": {"base_price": round(price_median, 2), "brand_factor": brand_factor, "condition_factor": condition_factor, "season_factor": season_factor, "liquidity_factor": round(liquidity_factor, 2), "market_volatility": round(coef_var, 2), "nb_comparables": nb_listings}
        }
    
    def _get_brand_factor(self, brand: Optional[str]) -> float:
        return BRAND_DEMAND_FACTOR.get(brand.lower(), 1.0) if brand else 1.0
    
    def _get_condition_factor(self, condition: str) -> float:
        return {"new_with_tags": 1.0, "new": 0.95, "like_new": 0.85, "good": 0.70, "fair": 0.55}.get(condition, 1.0)
    
    def _get_season_factor(self) -> float:
        from datetime import datetime
        return 1 + (SEASONALITY.get(datetime.now().month, 0) / 100)
    
    def _get_liquidity_factor(self, nb_listings: int, liquidity_score: float) -> float:
        return (min(1.0, nb_listings / 50) + liquidity_score / 100) / 2
    
    def _calculate_confidence(self, nb_listings: int, coef_var: float, source_type: str) -> float:
        confidence = 0.5
        if nb_listings >= 30: confidence += 0.25
        elif nb_listings >= 15: confidence += 0.15
        elif nb_listings >= 5: confidence += 0.05
        if coef_var < 0.2: confidence += 0.15
        elif coef_var < 0.3: confidence += 0.05
        elif coef_var > 0.5: confidence -= 0.1
        if source_type == "new": confidence += 0.1
        return max(0.2, min(0.95, confidence))
    
    def _no_data_fallback(self, buy_price: float, target_margin_pct: float) -> Dict[str, Any]:
        target_price = buy_price * (1 + target_margin_pct / 100)
        return {
            "recommended_price": round(target_price, 2),
            "price_range": {"min": round(buy_price * 1.05, 2), "optimal": round(target_price, 2), "max": round(buy_price * 1.30, 2)},
            "expected_margin": {"euro": round(target_price - buy_price, 2), "pct": target_margin_pct},
            "expected_sell_days": 14, "confidence": 0.2, "strategy": "estimated",
            "strategy_description": "Estimation sans données marché", "breakdown": {"note": "Pas de données Vinted"}
        }


pricing_engine = PricingEngine()

def calculate_smart_price(buy_price: float, vinted_stats: Dict[str, Any], brand: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    return pricing_engine.calculate_smart_price(buy_price, vinted_stats, brand, **kwargs)
