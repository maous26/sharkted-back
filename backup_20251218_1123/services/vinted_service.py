"""
Vinted Service - Wrapper pour le service de cache Vinted.
Utilise AI extraction pour optimiser les requêtes Vinted.
Prend en compte les tailles pour un pricing plus précis.
"""

from typing import Optional, Dict, Any, List
from loguru import logger

from app.services.vinted_cache_service import (
    get_cached_stats,
    set_cached_stats,
    get_scraper,
)
from app.services.ai_extraction_service import extract_product_name_ai


def _normalize_size(size: str) -> Optional[float]:
    """Normalise une taille en float EU."""
    if not size:
        return None
    
    size = str(size).strip().upper()
    
    # Ignorer les tailles enfants
    if 'C' in size or 'Y' in size or 'K' in size:
        return None
    
    # Extraire le numéro
    import re
    match = re.search(r'([\d]+\.?[\d]*)', size)
    if not match:
        return None
    
    num = float(match.group(1))
    
    # Format US -> EU approximatif (si taille < 20, probablement US)
    if num < 20:
        # US Men to EU
        us_to_eu = {
            6: 38.5, 6.5: 39, 7: 40, 7.5: 40.5,
            8: 41, 8.5: 42, 9: 42.5, 9.5: 43,
            10: 44, 10.5: 44.5, 11: 45, 11.5: 45.5,
            12: 46, 13: 47.5, 14: 48.5
        }
        if num in us_to_eu:
            return us_to_eu[num]
        # Si entre 7 et 14, c'est probablement US
        if 7 <= num <= 14:
            return num + 33  # Approximation US -> EU
    
    return num


def _get_best_size_for_search(sizes_available: List) -> Optional[str]:
    """
    Trouve la meilleure taille à utiliser pour la recherche Vinted.
    Priorise les tailles adultes courantes (40-45 EU).
    """
    if not sizes_available:
        return None
    
    # Normaliser et filtrer les tailles
    valid_sizes = []
    for size in sizes_available:
        if isinstance(size, dict):
            s = size.get('size') or size.get('eu') or size.get('value')
        else:
            s = str(size)
        
        norm = _normalize_size(s)
        if norm and 35 <= norm <= 50:  # Tailles adultes valides
            valid_sizes.append((s, norm))
    
    if not valid_sizes:
        return None
    
    # Tailles cibles par ordre de priorité (tailles courantes homme EU)
    target_sizes = [42, 43, 44, 41, 45, 40, 46, 42.5, 43.5, 44.5]
    
    for target in target_sizes:
        for original, normalized in valid_sizes:
            if abs(normalized - target) < 0.5:
                return original
    
    # Sinon prendre une taille au milieu
    valid_sizes.sort(key=lambda x: x[1])
    middle_idx = len(valid_sizes) // 2
    return valid_sizes[middle_idx][0]


async def get_vinted_stats_for_deal(
    product_name: str, 
    brand: Optional[str] = None, 
    sale_price: float = 0,
    sizes_available: Optional[List] = None
) -> Dict[str, Any]:
    """
    Obtient les stats Vinted pour un deal.
    Si des tailles sont disponibles, fait une recherche avec taille pour plus de précision.
    """
    # Utiliser l'AI pour extraire une query optimisée
    extraction = await extract_product_name_ai(product_name, brand)
    base_query = extraction.get('search_query', product_name)
    
    if not base_query or len(base_query) < 3:
        logger.warning(f"Query too short after AI extraction: '{base_query}'")
        return _empty_stats(base_query)
    
    # Trouver la meilleure taille pour la recherche
    size_used = _get_best_size_for_search(sizes_available) if sizes_available else None
    query = f"{base_query} {size_used}" if size_used else base_query
    
    logger.info(f"Vinted search: '{query}' (size={size_used}, AI from: '{product_name[:40]}...')")
    
    # Try cache first
    cached = get_cached_stats(query)
    if cached and cached.get('nb_listings', 0) > 0:
        cached['query_used'] = query
        cached['size_searched'] = size_used
        cached['ai_extraction'] = extraction
        logger.info(f"Cache HIT for: '{query}' - {cached.get('nb_listings', 0)} listings")
        return cached
    
    # Scrape Vinted
    try:
        scraper = get_scraper()
        result = scraper.search(query, limit=20)
        
        # Si pas de résultats avec taille, réessayer sans taille
        if (not result or result.get('nb_listings', 0) == 0) and size_used:
            logger.info(f"No results with size, retrying without: '{base_query}'")
            result = scraper.search(base_query, limit=20)
            size_used = None
            query = base_query
        
        if result and result.get('nb_listings', 0) > 0:
            # Calculate margin if sale_price provided
            if sale_price and result.get('price_median'):
                margin_euro = result['price_median'] - sale_price
                margin_pct = (margin_euro / sale_price) * 100 if sale_price > 0 else 0
                result['margin_euro'] = round(margin_euro, 2)
                result['margin_pct'] = round(margin_pct, 2)
            
            result['query_used'] = query
            result['size_searched'] = size_used
            result['ai_extraction'] = extraction
            
            # Cache les résultats
            set_cached_stats(query, result)
            
            logger.info(f"Vinted OK: '{query}' -> {result.get('nb_listings', 0)} listings, median={result.get('price_median')}, margin={result.get('margin_pct', 0):.1f}%")
            return result
        else:
            logger.warning(f"No Vinted results for: '{query}'")
            return _empty_stats(query)
        
    except Exception as e:
        logger.warning(f"Vinted scrape error for '{query}': {e}")
        return _empty_stats(query, str(e))


def _empty_stats(query: str, error: str = None) -> Dict[str, Any]:
    """Retourne des stats vides."""
    stats = {
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
        "source_type": "none",
        "sample_listings": [],
        "query_used": query,
    }
    if error:
        stats["_error"] = error
    return stats
