"""
Scraper KITH EU - Avec scoring COMPLET.
Version 3: Scoring complet avec recommended_price et estimated_sell_days.
"""

import httpx
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional

from loguru import logger

from app.scheduler import is_quiet_hours
from app.normalizers.item import DealItem
from app.services.scoring_service_hybrid import score_deal_hybrid  # Scoring hybride Vinted + fallback
from app.db.session import SessionLocal
from app.models.deal_score import DealScore
from app.repositories.deal_repository import DealRepository


KITH_BASE_URL = "https://eu.kith.com"
KITH_COLLECTIONS = [
    "footwear-sale",
    "apparel-sale", 
    "kids-footwear-sale",
    "kids-apparel-sale",
]
MIN_SCORE = 60
MIN_DISCOUNT = 30  # Minimum 30% de remise

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def persist_kith_deal_with_score(item: DealItem, score_data: Dict, session) -> Dict:
    """Persiste un deal KITH avec son score complet (recommended_price, estimated_sell_days)."""
    repo = DealRepository(session)
    existing = repo.get_by_source_and_id(item.source, item.external_id)
    was_existing = existing is not None

    deal = repo.upsert(item)
    deal_id = deal.id

    existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
    if existing_score:
        # Update - mettre à jour tous les champs
        existing_score.flip_score = score_data.get('flip_score', 0)
        existing_score.margin_score = score_data.get('margin_score', 0)
        existing_score.liquidity_score = score_data.get('liquidity_score', 0)
        existing_score.popularity_score = score_data.get('popularity_score', 0)
        existing_score.recommended_action = score_data.get('recommended_action')
        existing_score.recommended_price = score_data.get('recommended_price')
        existing_score.confidence = score_data.get('confidence')
        existing_score.explanation = score_data.get('explanation')
        existing_score.explanation_short = score_data.get('explanation_short')
        existing_score.risks = score_data.get('risks', [])
        existing_score.estimated_sell_days = score_data.get('estimated_sell_days')
        existing_score.score_breakdown = score_data.get('score_breakdown', {})
        existing_score.model_version = score_data.get('model_version', 'autonomous_v3')
        existing_score.updated_at = datetime.utcnow()
    else:
        # Create - avec tous les champs du scoring complet
        deal_score = DealScore(
            deal_id=deal_id,
            flip_score=score_data.get('flip_score', 0),
            margin_score=score_data.get('margin_score', 0),
            liquidity_score=score_data.get('liquidity_score', 0),
            popularity_score=score_data.get('popularity_score', 0),
            recommended_action=score_data.get('recommended_action'),
            recommended_price=score_data.get('recommended_price'),
            confidence=score_data.get('confidence'),
            explanation=score_data.get('explanation'),
            explanation_short=score_data.get('explanation_short'),
            risks=score_data.get('risks', []),
            score_breakdown=score_data.get('score_breakdown', {}),
            model_version=score_data.get('model_version', 'autonomous_v3'),
            estimated_sell_days=score_data.get('estimated_sell_days'),
        )
        session.add(deal_score)

    return {
        "id": deal_id,
        "action": "updated" if was_existing else "created",
        "recommended_price": score_data.get('recommended_price'),
        "estimated_sell_days": score_data.get('estimated_sell_days'),
    }


def collect_kith_collection(collection: str = "footwear-sale", limit: int = 250, min_score: int = MIN_SCORE) -> Dict[str, Any]:
    """Scrape une collection KITH avec scoring COMPLET."""
    url = f"{KITH_BASE_URL}/collections/{collection}/products.json"

    all_products = []
    page = 1

    try:
        while len(all_products) < limit:
            params = {"limit": min(250, limit - len(all_products)), "page": page}

            with httpx.Client(timeout=30) as client:
                response = client.get(url, params=params, headers=HEADERS)

                if response.status_code != 200:
                    logger.warning(f"KITH {collection}: HTTP {response.status_code}")
                    break

                data = response.json()
                products = data.get("products", [])

                if not products:
                    break

                all_products.extend(products)
                logger.info(f"KITH {collection} page {page}: {len(products)} products")

                if len(products) < 250:
                    break
                page += 1

        deals_saved = 0
        deals_skipped = 0
        deals_no_discount = 0

        session = SessionLocal()

        # Event loop pour le scoring async
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            for product in all_products:
                deal_item = parse_kith_product(product, collection)
                if not deal_item:
                    deals_no_discount += 1
                    continue

                # Vérifier discount minimum (30%)
                if not deal_item.discount_percent or deal_item.discount_percent < MIN_DISCOUNT:
                    deals_no_discount += 1
                    continue

                # Score COMPLET (async)
                deal_data = {
                    "product_name": deal_item.title,
                    "brand": deal_item.brand,
                    "model": deal_item.model,
                    "category": deal_item.category or "default",
                    "color": None,
                    "gender": deal_item.gender,
                    "discount_percent": deal_item.discount_percent,
                    "sizes_available": deal_item.sizes_available,
                    "sale_price": deal_item.price,
                    "original_price": deal_item.original_price,
                }
                score_result = loop.run_until_complete(score_deal_hybrid(deal_data, use_vinted=True, use_ai=True))
                flip_score = score_result.get('flip_score', 0)

                if flip_score < min_score:
                    deals_skipped += 1
                    continue

                persist_kith_deal_with_score(deal_item, score_result, session)
                session.commit()
                deals_saved += 1
                logger.info(f"KITH: {deal_item.title[:35]} | -{deal_item.discount_percent:.0f}% | Score: {flip_score:.1f} | Price: {score_result.get('recommended_price')}")

        finally:
            loop.close()
            session.close()
        
        return {
            "status": "success",
            "source": "kith",
            "collection": collection,
            "products_found": len(all_products),
            "deals_saved": deals_saved,
            "deals_skipped": deals_skipped,
            "deals_no_discount": deals_no_discount,
        }
        
    except Exception as e:
        logger.error(f"KITH {collection} error: {e}")
        return {"status": "error", "error": str(e), "collection": collection}


def parse_kith_product(product: Dict, collection: str) -> Optional[DealItem]:
    """Parse produit KITH avec extraction améliorée des prix."""
    try:
        product_id = str(product.get("id", ""))
        title = product.get("title", "")
        vendor = product.get("vendor", "")
        handle = product.get("handle", "")
        product_type = product.get("product_type", "")
        
        variants = product.get("variants", [])
        available_variants = [v for v in variants if v.get("available")]
        
        if not available_variants:
            return None
        
        # Trouver le meilleur prix parmi les variants
        best_price = None
        best_original = None
        best_discount = 0
        
        for variant in available_variants:
            price = float(variant.get("price", 0))
            compare_price = variant.get("compare_at_price")
            
            if compare_price:
                original = float(compare_price)
                if original > price:
                    discount = round((1 - price / original) * 100, 1)
                    if discount > best_discount:
                        best_price = price
                        best_original = original
                        best_discount = discount
            elif not best_price:
                best_price = price
        
        # Si pas de compare_at_price, essayer de déduire depuis les tags
        if not best_original:
            tags = product.get("tags", [])
            for tag in tags:
                tag_lower = tag.lower()
                # Chercher tags comme "sale", "50-off", etc.
                if "off" in tag_lower or "sale" in tag_lower:
                    # Essayer d'extraire un pourcentage
                    import re
                    pct_match = re.search(r'(\d+)(?:%|-off|off)', tag_lower)
                    if pct_match:
                        pct = int(pct_match.group(1))
                        if 10 <= pct <= 80:
                            best_discount = float(pct)
                            best_original = round(best_price / (1 - pct / 100), 2)
                            break
        
        # Si toujours pas de discount et c'est dans une collection "sale", 
        # on skip car pas de données de prix fiables
        if not best_discount and "sale" in collection:
            return None
        
        if not best_price:
            return None
        
        sizes = []
        for v in available_variants:
            size = v.get("option1") or v.get("title")
            if size and v.get("available") and size not in sizes:
                sizes.append(size)
        
        images = product.get("images", [])
        image_url = images[0].get("src") if images else None
        
        tags = product.get("tags", [])
        tags_lower = [t.lower() for t in tags]
        
        gender = "unisex"
        if "kids" in collection:
            gender = "kids"
        elif any("mens" in t or "men's" in t for t in tags_lower):
            gender = "men"
        elif any("womens" in t or "women's" in t for t in tags_lower):
            gender = "women"
        
        category = "footwear" if "footwear" in collection or "shoe" in product_type.lower() else "apparel"
        
        return DealItem(
            source="kith",
            external_id=f"{product_id}_kith",
            title=title,
            brand=vendor,
            model=handle,
            category=category,
            gender=gender,
            price=best_price,
            original_price=best_original,
            discount_percent=best_discount if best_discount > 0 else None,
            currency="EUR",
            url=f"{KITH_BASE_URL}/products/{handle}",
            image_url=image_url,
            sizes_available=sizes if sizes else None,
            seller_name="KITH EU",
        )
        
    except Exception as e:
        logger.error(f"KITH parse error: {e}")
        return None


def collect_all_kith(min_score: int = MIN_SCORE) -> Dict[str, Any]:
    """Scrape toutes les collections KITH - Skip pendant les heures de pause."""
    # Vérifier si on est dans les heures de pause (minuit-7h Paris)
    if is_quiet_hours():
        logger.info("KITH scraping SKIPPED (quiet hours: 00h-07h Paris)")
        return {"status": "skipped", "reason": "quiet_hours", "total_saved": 0}
    
    results = {"collections": {}, "total_saved": 0, "total_skipped": 0, "total_no_discount": 0}
    
    for collection in KITH_COLLECTIONS:
        result = collect_kith_collection(collection, min_score=min_score)
        results["collections"][collection] = result
        results["total_saved"] += result.get("deals_saved", 0)
        results["total_skipped"] += result.get("deals_skipped", 0)
        results["total_no_discount"] += result.get("deals_no_discount", 0)
    
    logger.info(f"KITH total: {results['total_saved']} saved, {results['total_skipped']} skipped, {results['total_no_discount']} no discount")
    return results
