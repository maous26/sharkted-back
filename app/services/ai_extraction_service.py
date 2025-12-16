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
        
        prompt = f"""Analyse ce produit pour une recherche Vinted précise.

Input Title: {clean_title}
Input Brand: {brand or "N/A"}

OBJECTIF: Extraire les termes clés qui définissent la valeur du modèle (Nom + Colorway iconique + SKU si présent).

RÈGLES STRICTES:
1. "search_query": Doit être la chaîne de recherche Vinted la plus efficace.
   - INCLURE: Modèle précis (ex: "Dunk Low"), Colorway SI c'est un surnom connu (ex: "Panda", "Bred", "UNC"), SKU s'il est dans le titre.
   - EXCLURE: Mots génériques (Homme/Femme/Taille/Basket), couleurs génériques (Blanc, Noir) SAUF si c'est le nom du modèle (ex: "Triple Black").
2. "sku": Extrait le code style (ex: DD1391-100) si présent, sinon null.
3. "colorway": Le nom du coloris si détectable.

Exemples:
- "Nike Dunk Low Retro Panda White Black" -> {{"brand": "Nike", "model": "Dunk Low Retro", "colorway": "Panda", "sku": null, "search_query": "Nike Dunk Low Panda"}}
- "Air Jordan 4 Retro Military Black DH6927-111" -> {{"brand": "Air Jordan", "model": "Jordan 4 Retro", "colorway": "Military Black", "sku": "DH6927-111", "search_query": "Jordan 4 Military Black"}}
- "Adidas Samba OG White Green" -> {{"brand": "Adidas", "model": "Samba OG", "colorway": "White Green", "sku": null, "search_query": "Adidas Samba OG"}}

OUTPUT: UNIQUEMENT le JSON minifié."""

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
