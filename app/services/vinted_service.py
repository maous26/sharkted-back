"""
Vinted Service - DÉSACTIVÉ
Ce service est désactivé pour éviter l'utilisation de proxies résidentiels non justifiés.
Le scoring est maintenant autonome et ne dépend plus de Vinted.
"""

from typing import Optional, Dict, Any
from loguru import logger


class VintedServiceDisabled:
    """Service Vinted désactivé - retourne toujours des valeurs vides."""
    
    def __init__(self):
        logger.warning("VintedService DÉSACTIVÉ - Scoring autonome actif")
    
    async def get_market_stats_hybrid(self, *args, **kwargs) -> Dict[str, Any]:
        """Retourne des stats vides - Vinted désactivé."""
        return self._empty_stats()
    
    async def get_market_stats(self, *args, **kwargs) -> Dict[str, Any]:
        """Retourne des stats vides - Vinted désactivé."""
        return self._empty_stats()
    
    def calculate_margin(self, *args, **kwargs):
        """Retourne marge 0 - Vinted désactivé."""
        return 0.0, 0.0
    
    def calculate_liquidity_score(self, *args, **kwargs) -> float:
        """Retourne liquidité 0 - Vinted désactivé."""
        return 0.0
    
    def _empty_stats(self) -> Dict[str, Any]:
        return {
            "nb_listings": 0,
            "price_min": 0,
            "price_max": 0,
            "price_avg": 0,
            "price_median": 0,
            "price_p25": 0,
            "price_p75": 0,
            "margin_euro": 0,
            "margin_pct": 0,
            "liquidity_score": 0,
            "coefficient_variation": 0,
            "source_type": "disabled",
            "sample_listings": [],
            "_disabled": True,
            "_message": "Vinted désactivé - scoring autonome actif"
        }


# Instance singleton
vinted_service = VintedServiceDisabled()


async def get_vinted_stats_for_deal(
    product_name: str, 
    brand: Optional[str] = None, 
    sale_price: float = 0
) -> Dict[str, Any]:
    """
    Fonction wrapper - DÉSACTIVÉE
    Retourne des stats vides car le scoring est maintenant autonome.
    """
    logger.debug(f"get_vinted_stats_for_deal appelé mais DÉSACTIVÉ pour: {product_name}")
    return vinted_service._empty_stats()
