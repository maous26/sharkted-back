"""
Service Scoring HYBRIDE - Combine Vinted réel + Fallback statistique + IA

Architecture:
1. Extraction IA du nom produit (nettoie référence, extrait couleur/modèle)
2. Recherche Vinted avec taille/couleur pour prix réel
3. Si Vinted échoue → Fallback statistique basé sur sale_price
4. Enrichissement IA pour ajuster le score
5. Tracking ML pour feedback loop

Règles de pricing Vinted:
- Couleurs classiques (noir/blanc/gris) = plus liquides, prix médian
- Couleurs flashy = moins demandées, prix -10-15%
- Tailles standard (40-44) = meilleures ventes
"""

import os
import re
import json
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from loguru import logger

# Import services existants
from app.services.vinted_service import get_vinted_stats_for_deal
from app.services.ai_extraction_service import extract_product_name_ai
from app.services.scoring_service import (
    BRAND_RESALE_DATA,
    EXCLUDED_BRANDS,
    PREMIUM_TEXTILE_BRANDS,
    CATEGORY_CONFIG,
    SAFE_COLORS,
    RISKY_COLORS,
    is_brand_excluded,
    is_premium_brand,
)


# =============================================================================
# CONSTANTES PRICING
# =============================================================================

# Décote réaliste sur Vinted (produit neuf avec étiquette)
VINTED_DECOTE = {
    "neuf_etiquette": 0.85,    # -15% vs prix magasin
    "neuf_sans_etiquette": 0.75,  # -25%
    "tres_bon_etat": 0.65,     # -35%
}

# Ajustement couleur pour sneakers
COLOR_PRICE_ADJUSTMENT = {
    "premium": ["black", "noir", "white", "blanc", "sail", "cream"],  # +5%
    "neutral": ["grey", "gris", "navy", "blue", "bleu", "beige"],     # 0%
    "risky": ["pink", "rose", "yellow", "jaune", "orange", "violet", "fluo", "neon"],  # -15%
}

# Frais Vinted
VINTED_FEES = {
    "commission": 0.05,     # 5% commission vendeur
    "payment": 0.03,        # 3% frais de paiement
    "shipping_avg": 5.50,   # Frais d'envoi moyens (Mondial Relay)
}


# =============================================================================
# EXTRACTION IA AMÉLIORÉE
# =============================================================================

async def extract_product_details_ai(
    title: str,
    brand: Optional[str] = None,
    color: Optional[str] = None,
    sizes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Extraction IA améliorée avec détection couleur et modèle exact.
    Optimisé pour la recherche Vinted.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _extract_with_enhanced_rules(title, brand, color)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Analyse ce produit sneaker/streetwear pour optimiser la recherche sur Vinted.

Titre complet: {title}
Marque indiquée: {brand or "non spécifiée"}
Couleur indiquée: {color or "non spécifiée"}

Réponds UNIQUEMENT en JSON valide (pas de markdown, pas de ```):
{{
    "brand": "marque exacte",
    "model": "modèle exact sans couleur ni genre",
    "colorway": "nom du colorway si identifiable (ex: Panda, University Blue, etc.)",
    "color_category": "premium|neutral|risky",
    "is_collab": true/false,
    "collab_brand": "marque collab si applicable",
    "search_query_base": "query sans couleur pour Vinted",
    "search_query_color": "query avec couleur pour prix précis"
}}

Règles importantes:
1. Pour Nike Dunk: identifier le colorway (Panda = noir/blanc, Syracuse = orange, etc.)
2. color_category: premium = noir/blanc/gris/sail, neutral = bleu/beige, risky = rose/jaune/orange/fluo
3. search_query_base: marque + modèle seulement
4. search_query_color: marque + modèle + couleur principale pour prix spécifique

Exemples:
- "Nike Dunk Low Retro White Black Panda" -> colorway: "Panda", color_category: "premium"
- "Nike Dunk Low Syracuse" -> colorway: "Syracuse", color_category: "risky" (orange)
- "Adidas Campus 00s Core Black" -> colorway: "Core Black", color_category: "premium"
"""

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()

        # Nettoyer le markdown si présent
        if response_text.startswith("```"):
            response_text = re.sub(r'^```\w*\n?', '', response_text)
            response_text = re.sub(r'\n?```$', '', response_text)

        data = json.loads(response_text)

        result = {
            "original_title": title,
            "brand": data.get("brand", brand),
            "model": data.get("model"),
            "colorway": data.get("colorway"),
            "color_category": data.get("color_category", "neutral"),
            "is_collab": data.get("is_collab", False),
            "collab_brand": data.get("collab_brand"),
            "search_query_base": data.get("search_query_base", f"{brand} {title}"[:50]),
            "search_query_color": data.get("search_query_color"),
            "method": "ai_enhanced"
        }

        logger.info(f"AI extraction: '{title[:40]}' -> model='{result['model']}', colorway='{result['colorway']}', category='{result['color_category']}'")
        return result

    except Exception as e:
        logger.warning(f"AI extraction failed: {e}")
        return _extract_with_enhanced_rules(title, brand, color)


def _extract_with_enhanced_rules(
    title: str,
    brand: Optional[str] = None,
    color: Optional[str] = None
) -> Dict[str, Any]:
    """Extraction basée sur règles améliorées (fallback)."""
    title_lower = title.lower()

    # Détection colorway connus
    known_colorways = {
        "panda": ("premium", "Panda"),
        "white black": ("premium", "Panda"),
        "noir blanc": ("premium", "Panda"),
        "syracuse": ("risky", "Syracuse"),
        "university blue": ("neutral", "University Blue"),
        "unc": ("neutral", "UNC"),
        "chicago": ("premium", "Chicago"),
        "bred": ("premium", "Bred"),
        "triple white": ("premium", "Triple White"),
        "triple black": ("premium", "Triple Black"),
    }

    detected_colorway = None
    color_category = "neutral"

    for pattern, (category, colorway_name) in known_colorways.items():
        if pattern in title_lower:
            detected_colorway = colorway_name
            color_category = category
            break

    # Si pas de colorway connu, détecter via couleur
    if not detected_colorway and color:
        color_lower = color.lower()
        if any(c in color_lower for c in COLOR_PRICE_ADJUSTMENT["premium"]):
            color_category = "premium"
        elif any(c in color_lower for c in COLOR_PRICE_ADJUSTMENT["risky"]):
            color_category = "risky"

    # Nettoyer le titre pour la recherche
    clean_patterns = [
        r'\s+(Homme|Femme|Womens|Mens|Unisex|Junior|GS|PS|TD)\s*',
        r'\s*-\s*$',
        r'\s+$',
    ]

    search_query = title
    for pattern in clean_patterns:
        search_query = re.sub(pattern, ' ', search_query, flags=re.IGNORECASE)
    search_query = ' '.join(search_query.split())[:60]

    return {
        "original_title": title,
        "brand": brand,
        "model": search_query,
        "colorway": detected_colorway,
        "color_category": color_category,
        "is_collab": False,
        "search_query_base": search_query,
        "search_query_color": f"{search_query} {color}" if color else search_query,
        "method": "rules_enhanced"
    }


# =============================================================================
# SCORING HYBRIDE
# =============================================================================

class HybridScoringEngine:
    """
    Moteur de scoring hybride combinant:
    1. Prix réels Vinted (si disponible)
    2. Estimation statistique (fallback)
    3. Ajustements IA
    """

    async def score_deal(
        self,
        product_name: str,
        brand: Optional[str],
        original_price: float,
        sale_price: float,
        discount_percent: float,
        category: str = "sneakers",
        color: Optional[str] = None,
        sizes_available: Optional[List[str]] = None,
        use_vinted: bool = True,
        use_ai: bool = True,
    ) -> Dict[str, Any]:
        """
        Score un deal avec le système hybride.
        """
        # 1. Extraction IA des détails produit
        if use_ai:
            product_details = await extract_product_details_ai(
                product_name, brand, color, sizes_available
            )
        else:
            product_details = _extract_with_enhanced_rules(product_name, brand, color)

        # 2. Recherche Vinted pour prix réels
        vinted_stats = None
        vinted_source = "none"

        if use_vinted:
            vinted_stats = await self._get_vinted_pricing(
                product_details, sale_price, sizes_available
            )
            if vinted_stats and vinted_stats.get("nb_listings", 0) > 0:
                vinted_source = "vinted_real"

        # 3. Calcul du prix de revente
        if vinted_stats and vinted_stats.get("price_median", 0) > 0:
            # Utiliser les prix Vinted réels
            pricing = self._calculate_pricing_from_vinted(
                vinted_stats, sale_price, product_details
            )
        else:
            # Fallback statistique
            pricing = self._calculate_pricing_fallback(
                original_price, sale_price, brand, product_details
            )
            vinted_source = "fallback_stats"

        # 4. Calcul du FlipScore
        flip_score, components = self._calculate_flip_score(
            discount_percent=discount_percent,
            margin_pct=pricing["margin_pct"],
            brand=brand,
            color_category=product_details.get("color_category", "neutral"),
            sizes_available=sizes_available,
            vinted_liquidity=vinted_stats.get("liquidity_score", 0) if vinted_stats else 0,
        )

        # 5. Recommandation et risques
        recommendation, confidence = self._get_recommendation(
            flip_score, pricing["margin_pct"], pricing["margin_euro"], discount_percent
        )

        risks = self._identify_risks(
            pricing, product_details, brand, vinted_stats
        )

        estimated_days = self._estimate_sell_days(
            flip_score, brand, vinted_stats, product_details
        )

        explanation = self._generate_explanation(
            discount_percent, pricing, flip_score, recommendation,
            brand, product_details, vinted_source
        )

        return {
            "flip_score": round(flip_score, 1),
            "recommended_action": recommendation,
            "recommended_price": pricing["recommended_price"],
            "recommended_price_range": {
                "aggressive": pricing["price_aggressive"],
                "optimal": pricing["recommended_price"],
                "patient": pricing["price_patient"],
            },
            "confidence": confidence,
            "explanation": explanation,
            "explanation_short": explanation[:200] if len(explanation) > 200 else explanation,
            "risks": risks,
            "estimated_sell_days": estimated_days,
            "model_version": "hybrid_v1",
            "vinted_source_type": vinted_source,

            # Détails scoring
            "score_breakdown": {
                **components,
                "estimated_margin_euro": pricing["margin_euro"],
                "estimated_margin_pct": pricing["margin_pct"],
            },

            # Scores individuels
            "margin_score": components.get("margin_score", 0),
            "liquidity_score": vinted_stats.get("liquidity_score", 0) if vinted_stats else 0,
            "popularity_score": components.get("brand_score", 0),
            "discount_score": components.get("discount_score", 0),

            # Méta
            "margin_euro": pricing["margin_euro"],
            "margin_pct": pricing["margin_pct"],
            "product_details": product_details,
            "vinted_stats": vinted_stats if vinted_stats else None,
        }

    async def _get_vinted_pricing(
        self,
        product_details: Dict,
        sale_price: float,
        sizes_available: Optional[List[str]]
    ) -> Optional[Dict]:
        """Récupère les prix Vinted réels."""
        try:
            # Essayer d'abord avec la query couleur pour plus de précision
            query = product_details.get("search_query_color") or product_details.get("search_query_base")

            if not query:
                return None

            stats = await get_vinted_stats_for_deal(
                product_name=query,
                brand=product_details.get("brand"),
                sale_price=sale_price,
                sizes_available=sizes_available
            )

            # Si pas de résultats avec couleur, essayer sans
            if stats.get("nb_listings", 0) < 3 and product_details.get("search_query_base"):
                base_query = product_details["search_query_base"]
                if base_query != query:
                    stats_base = await get_vinted_stats_for_deal(
                        product_name=base_query,
                        brand=product_details.get("brand"),
                        sale_price=sale_price,
                        sizes_available=sizes_available
                    )
                    if stats_base.get("nb_listings", 0) > stats.get("nb_listings", 0):
                        stats = stats_base

            return stats

        except Exception as e:
            logger.warning(f"Vinted lookup failed: {e}")
            return None

    def _calculate_pricing_from_vinted(
        self,
        vinted_stats: Dict,
        sale_price: float,
        product_details: Dict
    ) -> Dict[str, float]:
        """Calcule le pricing basé sur les données Vinted réelles."""
        price_median = vinted_stats.get("price_median", 0)
        price_p25 = vinted_stats.get("price_p25", price_median * 0.85)
        price_p75 = vinted_stats.get("price_p75", price_median * 1.15)

        # Ajustement couleur
        color_category = product_details.get("color_category", "neutral")
        color_multiplier = 1.0
        if color_category == "premium":
            color_multiplier = 1.05  # +5% pour noir/blanc
        elif color_category == "risky":
            color_multiplier = 0.85  # -15% pour couleurs flashy

        adjusted_median = price_median * color_multiplier

        # Stratégies de prix
        price_aggressive = round(adjusted_median * 0.88, 2)  # Vente rapide
        price_optimal = round(adjusted_median * 0.95, 2)     # Prix équilibré
        price_patient = round(adjusted_median * 1.02, 2)     # Prix max

        # Calcul marge nette (après frais Vinted)
        fees_pct = VINTED_FEES["commission"] + VINTED_FEES["payment"]
        shipping = VINTED_FEES["shipping_avg"]

        net_after_fees = price_optimal * (1 - fees_pct) - shipping
        margin_euro = round(net_after_fees - sale_price, 2)
        margin_pct = round((margin_euro / sale_price) * 100, 1) if sale_price > 0 else 0

        return {
            "recommended_price": price_optimal,
            "price_aggressive": price_aggressive,
            "price_patient": price_patient,
            "price_median_raw": price_median,
            "color_adjustment": color_multiplier,
            "margin_euro": margin_euro,
            "margin_pct": margin_pct,
            "source": "vinted_real",
        }

    def _calculate_pricing_fallback(
        self,
        original_price: float,
        sale_price: float,
        brand: Optional[str],
        product_details: Dict
    ) -> Dict[str, float]:
        """Fallback: estimation statistique basée sur sale_price."""

        # Obtenir le multiplicateur de marque
        brand_info = self._get_brand_info(brand)
        base_multiplier = brand_info.get("resale_multiplier", 0.90)

        # IMPORTANT: Baser sur sale_price, pas original_price
        # Un produit soldé -50% ne se revend pas au prix neuf !
        # Formule: sale_price * (1 + marge_cible)

        # Marge cible selon la catégorie couleur
        color_category = product_details.get("color_category", "neutral")

        if color_category == "premium":
            target_margin = 0.25  # 25% de marge cible
        elif color_category == "neutral":
            target_margin = 0.18  # 18% de marge cible
        else:  # risky
            target_margin = 0.10  # 10% seulement

        # Ajuster selon le tier de la marque
        tier = brand_info.get("tier", "B")
        tier_bonus = {"S": 0.10, "A": 0.05, "B": 0, "C": -0.05, "X": -0.15}
        target_margin += tier_bonus.get(tier, 0)

        # Prix de revente estimé
        estimated_resale = sale_price * (1 + target_margin)

        # Plafonner à un % du prix original (réalisme)
        max_resale = original_price * VINTED_DECOTE["neuf_etiquette"] if original_price > 0 else estimated_resale * 1.2
        estimated_resale = min(estimated_resale, max_resale)

        # Stratégies de prix
        price_aggressive = round(estimated_resale * 0.90, 2)
        price_optimal = round(estimated_resale, 2)
        price_patient = round(estimated_resale * 1.08, 2)

        # Calcul marge nette
        fees_pct = VINTED_FEES["commission"] + VINTED_FEES["payment"]
        shipping = VINTED_FEES["shipping_avg"]

        net_after_fees = price_optimal * (1 - fees_pct) - shipping
        margin_euro = round(net_after_fees - sale_price, 2)
        margin_pct = round((margin_euro / sale_price) * 100, 1) if sale_price > 0 else 0

        return {
            "recommended_price": price_optimal,
            "price_aggressive": price_aggressive,
            "price_patient": price_patient,
            "target_margin": target_margin,
            "color_adjustment": 1.0,
            "margin_euro": margin_euro,
            "margin_pct": margin_pct,
            "source": "fallback_stats",
        }

    def _get_brand_info(self, brand: Optional[str]) -> Dict[str, Any]:
        """Récupère les infos de la marque."""
        if not brand:
            return {"tier": "X", "resale_multiplier": 0.50, "demand_score": 10, "avg_days_to_sell": 60, "excluded": True}

        brand_lower = brand.lower().strip()

        if is_brand_excluded(brand):
            return {"tier": "X", "resale_multiplier": 0.40, "demand_score": 5, "avg_days_to_sell": 90, "excluded": True}

        for key, data in BRAND_RESALE_DATA.items():
            if key in brand_lower or brand_lower in key:
                return {**data, "excluded": False}

        if is_premium_brand(brand):
            return {"tier": "A", "resale_multiplier": 1.05, "demand_score": 75, "avg_days_to_sell": 12, "excluded": False}

        return {"tier": "B", "resale_multiplier": 0.90, "demand_score": 50, "avg_days_to_sell": 20, "excluded": False}

    def _calculate_flip_score(
        self,
        discount_percent: float,
        margin_pct: float,
        brand: Optional[str],
        color_category: str,
        sizes_available: Optional[List[str]],
        vinted_liquidity: float,
    ) -> Tuple[float, Dict[str, Any]]:
        """Calcule le FlipScore hybride."""

        # 1. Score discount (30%)
        if discount_percent >= 60:
            discount_score = 100
        elif discount_percent >= 50:
            discount_score = 90
        elif discount_percent >= 40:
            discount_score = 75
        elif discount_percent >= 30:
            discount_score = 60
        else:
            discount_score = max(20, discount_percent * 1.5)

        # 2. Score marge (35%) - le plus important
        if margin_pct >= 35:
            margin_score = 100
        elif margin_pct >= 25:
            margin_score = 85
        elif margin_pct >= 15:
            margin_score = 70
        elif margin_pct >= 10:
            margin_score = 55
        elif margin_pct >= 5:
            margin_score = 40
        elif margin_pct >= 0:
            margin_score = 25
        else:
            margin_score = max(0, 15 + margin_pct)  # Négatif = score bas

        # 3. Score marque (20%)
        brand_info = self._get_brand_info(brand)
        tier_scores = {"S": 100, "A": 80, "B": 55, "C": 35, "X": 10}
        brand_score = tier_scores.get(brand_info.get("tier", "B"), 50)

        # 4. Score contextuel (15%)
        contextual_score = 50

        # Bonus couleur
        if color_category == "premium":
            contextual_score += 20
        elif color_category == "risky":
            contextual_score -= 15

        # Bonus tailles
        if sizes_available:
            standard_sizes = {"40", "41", "42", "43", "44", "45", "M", "L", "XL"}
            matching = set(str(s).upper() for s in sizes_available) & standard_sizes
            if len(matching) >= 3:
                contextual_score += 15
            elif len(matching) >= 1:
                contextual_score += 5

        # Bonus liquidité Vinted
        if vinted_liquidity >= 50:
            contextual_score += 10

        contextual_score = min(100, max(0, contextual_score))

        # Score final pondéré
        flip_score = (
            discount_score * 0.30 +
            margin_score * 0.35 +
            brand_score * 0.20 +
            contextual_score * 0.15
        )

        # Bonus/malus final
        if margin_pct >= 30 and discount_percent >= 50:
            flip_score += 5  # Combo jackpot
        if brand_info.get("excluded"):
            flip_score = min(flip_score, 25)  # Plafond marques exclues

        flip_score = min(100, max(0, flip_score))

        components = {
            "discount_score": round(discount_score, 1),
            "margin_score": round(margin_score, 1),
            "brand_score": round(brand_score, 1),
            "contextual_score": round(contextual_score, 1),
        }

        return flip_score, components

    def _get_recommendation(
        self,
        flip_score: float,
        margin_pct: float,
        margin_euro: float,
        discount_percent: float
    ) -> Tuple[str, float]:
        """Détermine la recommandation."""

        # Règles strictes
        if margin_pct < 5:
            return "ignore", 0.3

        if flip_score >= 75 and margin_pct >= 20:
            return "buy", min(0.95, 0.75 + flip_score / 400)
        elif flip_score >= 65 and margin_pct >= 15:
            return "buy", min(0.85, 0.65 + flip_score / 400)
        elif flip_score >= 55 and margin_pct >= 10:
            return "watch", 0.60
        elif flip_score >= 45:
            return "watch", 0.50
        else:
            return "ignore", 0.35

    def _identify_risks(
        self,
        pricing: Dict,
        product_details: Dict,
        brand: Optional[str],
        vinted_stats: Optional[Dict]
    ) -> List[str]:
        """Identifie les risques."""
        risks = []

        brand_info = self._get_brand_info(brand)

        if brand_info.get("excluded"):
            risks.append("⛔ Marque non recommandée (distributeur/premier prix)")
            return risks

        if pricing["margin_pct"] < 10:
            risks.append(f"Marge faible ({pricing['margin_pct']:.0f}%)")

        if pricing["margin_euro"] < 8:
            risks.append(f"Profit limité ({pricing['margin_euro']:.0f}€)")

        if product_details.get("color_category") == "risky":
            risks.append("Couleur atypique - revente plus lente")

        if vinted_stats and vinted_stats.get("nb_listings", 0) < 5:
            risks.append("Peu d'annonces Vinted - estimation incertaine")

        if vinted_stats and vinted_stats.get("coefficient_variation", 0) > 30:
            risks.append("Prix très variables sur Vinted")

        if brand_info.get("tier") == "C":
            risks.append("Marque peu recherchée")

        return risks

    def _estimate_sell_days(
        self,
        flip_score: float,
        brand: Optional[str],
        vinted_stats: Optional[Dict],
        product_details: Dict
    ) -> int:
        """Estime le délai de vente."""
        brand_info = self._get_brand_info(brand)
        base_days = brand_info.get("avg_days_to_sell", 20)

        # Ajustement score
        if flip_score >= 80:
            base_days = max(3, base_days - 5)
        elif flip_score >= 70:
            base_days = max(5, base_days - 2)
        elif flip_score < 50:
            base_days += 10

        # Ajustement couleur
        if product_details.get("color_category") == "premium":
            base_days = max(3, base_days - 3)
        elif product_details.get("color_category") == "risky":
            base_days += 7

        # Ajustement liquidité Vinted
        if vinted_stats and vinted_stats.get("liquidity_score", 0) >= 70:
            base_days = max(3, base_days - 3)

        return min(60, max(3, base_days))

    def _generate_explanation(
        self,
        discount_percent: float,
        pricing: Dict,
        flip_score: float,
        recommendation: str,
        brand: Optional[str],
        product_details: Dict,
        vinted_source: str
    ) -> str:
        """Génère l'explication."""
        parts = []

        # Promo
        if discount_percent >= 50:
            parts.append(f"Promo exceptionnelle (-{discount_percent:.0f}%)")
        elif discount_percent >= 35:
            parts.append(f"Bonne promo (-{discount_percent:.0f}%)")

        # Source pricing
        if vinted_source == "vinted_real":
            parts.append("prix basé sur Vinted réel")
        else:
            parts.append("estimation statistique")

        # Marge
        margin = pricing["margin_pct"]
        if margin >= 25:
            parts.append(f"marge excellente (~{margin:.0f}%)")
        elif margin >= 15:
            parts.append(f"marge correcte (~{margin:.0f}%)")
        elif margin >= 5:
            parts.append(f"marge faible (~{margin:.0f}%)")
        else:
            parts.append(f"marge très faible (~{margin:.0f}%)")

        # Couleur
        color_cat = product_details.get("color_category", "neutral")
        colorway = product_details.get("colorway")
        if colorway:
            if color_cat == "premium":
                parts.append(f"colorway recherché ({colorway})")
            elif color_cat == "risky":
                parts.append(f"colorway moins demandé ({colorway})")

        # Conclusion
        if recommendation == "buy":
            conclusion = "Deal recommandé."
        elif recommendation == "watch":
            conclusion = "À surveiller."
        else:
            conclusion = "Pass recommandé."

        return f"{', '.join(parts)}. {conclusion}"


# =============================================================================
# INSTANCE SINGLETON ET FONCTION HELPER
# =============================================================================

_hybrid_engine = None

def get_hybrid_engine() -> HybridScoringEngine:
    global _hybrid_engine
    if _hybrid_engine is None:
        _hybrid_engine = HybridScoringEngine()
    return _hybrid_engine


async def score_deal_hybrid(
    deal_data: Dict[str, Any],
    use_vinted: bool = True,
    use_ai: bool = True
) -> Dict[str, Any]:
    """
    Fonction helper pour scorer un deal avec le système hybride.

    Args:
        deal_data: Dict avec product_name, brand, original_price, sale_price, etc.
        use_vinted: Activer la recherche Vinted
        use_ai: Activer l'enrichissement IA
    """
    engine = get_hybrid_engine()

    return await engine.score_deal(
        product_name=deal_data.get("product_name") or deal_data.get("title", ""),
        brand=deal_data.get("brand"),
        original_price=deal_data.get("original_price", 0) or deal_data.get("price", 0),
        sale_price=deal_data.get("sale_price", 0) or deal_data.get("price", 0),
        discount_percent=deal_data.get("discount_percent", 0) or 0,
        category=deal_data.get("category", "sneakers"),
        color=deal_data.get("color"),
        sizes_available=deal_data.get("sizes_available"),
        use_vinted=use_vinted,
        use_ai=use_ai,
    )
