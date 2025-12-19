"""
Service AI Extraction - Extraction intelligente des noms de produits
Utilise Claude Haiku pour nettoyer et normaliser les titres produits
"""

import os
import re
import html
from typing import Optional, Dict, Any
from functools import lru_cache
import hashlib

from loguru import logger

# Cache en memoire pour eviter les appels repetes
_extraction_cache: Dict[str, Dict[str, Any]] = {}


def _get_cache_key(title: str, brand: Optional[str]) -> str:
    """Genere une cle de cache unique."""
    content = f"{title}|{brand or ''}".lower().strip()
    return hashlib.md5(content.encode()).hexdigest()


def _clean_html_entities(text: str) -> str:
    """Nettoie les entites HTML."""
    if not text:
        return text
    text = html.unescape(text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&\w+;', '', text)
    return text.strip()


def _extract_with_rules(title: str, brand: Optional[str] = None) -> Dict[str, Any]:
    """Extraction basee sur des regles (fallback sans IA)."""
    title = _clean_html_entities(title)
    
    remove_patterns = [
        r'\s+(Homme|Femme|Womens|Mens|Unisex)\s*',
        r'\s+(Noir|Blanc|Bleu|Rouge|Gris|Vert|Rose|Marron|Beige|Grey|Black|White|Blue|Red|Green|Pink|Brown)\s*$',
        r'\s*-\s*(size\?|jd)?\s*exclusive\s*$',
        r'\s*\([^)]*\)\s*$',
        r'\s+$',
    ]
    
    clean_title = title
    for pattern in remove_patterns:
        clean_title = re.sub(pattern, ' ', clean_title, flags=re.IGNORECASE)
    
    clean_title = ' '.join(clean_title.split())
    
    detected_brand = brand
    if not detected_brand:
        brand_patterns = [
            r'^(Nike|Adidas|Jordan|New Balance|Puma|Reebok|Asics|Converse|Vans|Salomon|Saucony|On Running|Hoka|Brooks)\b',
        ]
        for pattern in brand_patterns:
            match = re.search(pattern, clean_title, re.IGNORECASE)
            if match:
                detected_brand = match.group(1)
                break
    
    return {
        "original_title": title,
        "clean_name": clean_title,
        "brand": detected_brand,
        "search_query": clean_title,
        "method": "rules"
    }


async def extract_product_name_ai(title: str, brand: Optional[str] = None) -> Dict[str, Any]:
    """Extraction intelligente du nom de produit avec Claude Haiku."""
    cache_key = _get_cache_key(title, brand)
    if cache_key in _extraction_cache:
        result = _extraction_cache[cache_key].copy()
        result["method"] = "cache"
        return result
    
    clean_title = _clean_html_entities(title)
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("No ANTHROPIC_API_KEY, using rules-based extraction")
        result = _extract_with_rules(clean_title, brand)
        _extraction_cache[cache_key] = result
        return result
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""Extrait les informations de ce produit sneaker/streetwear pour une recherche Vinted.

Titre: {clean_title}
Marque indiquee: {brand or "non specifiee"}

Reponds UNIQUEMENT en JSON (pas de markdown):
{{"brand": "marque", "model": "modele sans couleur/genre", "search_query": "query optimale pour Vinted"}}

Regles:
- search_query: marque + modele seulement (pas de couleur, pas de genre, pas de taille)
- model: nom du modele sans Homme/Femme/Womens/Mens ni couleurs
- Si collab, inclure les deux marques dans search_query

Exemples:
- Nike Air Force 1 Low Femme Blanc -> {{"brand": "Nike", "model": "Air Force 1 Low", "search_query": "Nike Air Force 1 Low"}}
- New Balance 2002R Protection Pack -> {{"brand": "New Balance", "model": "2002R Protection Pack", "search_query": "New Balance 2002R"}}"""

        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        
        import json
        response_text = response.content[0].text.strip()
        
        if response_text.startswith("```"):
            response_text = re.sub(r'^```\w*\n?', '', response_text)
            response_text = re.sub(r'\n?```$', '', response_text)
        
        data = json.loads(response_text)
        
        result = {
            "original_title": title,
            "clean_name": f"{data.get('brand', '')} {data.get('model', '')}".strip(),
            "brand": data.get("brand", brand),
            "model": data.get("model"),
            "search_query": data.get("search_query", clean_title),
            "method": "ai"
        }
        
        _extraction_cache[cache_key] = result
        logger.info(f"AI extraction: '{title[:50]}...' -> '{result['search_query']}'")
        
        return result
        
    except Exception as e:
        logger.warning(f"AI extraction failed, falling back to rules: {e}")
        result = _extract_with_rules(clean_title, brand)
        _extraction_cache[cache_key] = result
        return result


def get_optimized_search_query(title: str, brand: Optional[str] = None) -> str:
    """Version synchrone simple pour obtenir la query optimisee."""
    cache_key = _get_cache_key(title, brand)
    if cache_key in _extraction_cache:
        return _extraction_cache[cache_key].get("search_query", title)
    
    result = _extract_with_rules(title, brand)
    _extraction_cache[cache_key] = result
    return result.get("search_query", title)


def get_cache_stats() -> Dict[str, int]:
    return {
        "cache_size": len(_extraction_cache),
        "ai_extractions": sum(1 for v in _extraction_cache.values() if v.get("method") == "ai"),
        "rules_extractions": sum(1 for v in _extraction_cache.values() if v.get("method") == "rules"),
    }
