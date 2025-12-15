"""
Scraper KITH EU - Avec scoring autonome immédiat.
"""

import httpx
from datetime import datetime
from typing import Dict, Any, List, Optional

from loguru import logger

from app.normalizers.item import DealItem
from app.services.autonomous_scoring_service import score_deal_autonomous
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def persist_kith_deal_with_score(item: DealItem, score_data: Dict, session) -> Dict:
    """Persiste un deal KITH avec son score."""
    repo = DealRepository(session)
    existing = repo.get_by_source_and_id(item.source, item.external_id)
    was_existing = existing is not None
    
    deal = repo.upsert(item)
    deal_id = deal.id
    
    existing_score = session.query(DealScore).filter(DealScore.deal_id == deal_id).first()
    if not existing_score:
        deal_score = DealScore(
            deal_id=deal_id,
            flip_score=score_data.get('flip_score', 0),
            margin_score=score_data.get('discount_score'),
            liquidity_score=score_data.get('brand_score'),
            popularity_score=score_data.get('model_score'),
            recommended_action=score_data.get('recommended_action'),
            confidence=score_data.get('confidence'),
            explanation=score_data.get('explanation'),
            explanation_short=score_data.get('explanation_short'),
            risks=score_data.get('risks', []),
            score_breakdown=score_data.get('score_breakdown', {}),
            model_version='autonomous_v1',
        )
        session.add(deal_score)
    
    return {"id": deal_id, "action": "updated" if was_existing else "created"}


def collect_kith_collection(collection: str = "footwear-sale", limit: int = 250, min_score: int = MIN_SCORE) -> Dict[str, Any]:
    """Scrape une collection KITH avec scoring autonome."""
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
        
        session = SessionLocal()
        
        try:
            for product in all_products:
                deal_item = parse_kith_product(product, collection)
                if not deal_item:
                    continue
                
                # Score autonome
                deal_data = {
                    "title": deal_item.title,
                    "brand": deal_item.brand,
                    "model": deal_item.model,
                    "category": deal_item.category,
                    "discount_percent": deal_item.discount_percent,
                    "price": deal_item.price,
                    "sizes_available": deal_item.sizes_available,
                }
                score_result = score_deal_autonomous(deal_data)
                flip_score = score_result.get('flip_score', 0)
                
                if flip_score < min_score:
                    deals_skipped += 1
                    continue
                
                persist_kith_deal_with_score(deal_item, score_result, session)
                session.commit()
                deals_saved += 1
                logger.info(f"KITH: {deal_item.title[:35]} | Score: {flip_score:.1f}")
                
        finally:
            session.close()
        
        return {
            "status": "success",
            "source": "kith",
            "collection": collection,
            "products_found": len(all_products),
            "deals_saved": deals_saved,
            "deals_skipped": deals_skipped,
        }
        
    except Exception as e:
        logger.error(f"KITH {collection} error: {e}")
        return {"status": "error", "error": str(e), "collection": collection}


def parse_kith_product(product: Dict, collection: str) -> Optional[DealItem]:
    """Parse produit KITH."""
    try:
        product_id = str(product.get("id", ""))
        title = product.get("title", "")
        vendor = product.get("vendor", "")
        handle = product.get("handle", "")
        
        variants = product.get("variants", [])
        available_variants = [v for v in variants if v.get("available")]
        
        if not available_variants:
            return None
        
        first_variant = available_variants[0]
        price = float(first_variant.get("price", 0))
        compare_price = first_variant.get("compare_at_price")
        original_price = float(compare_price) if compare_price else None
        
        if not original_price or original_price <= price:
            return None
        
        discount_pct = round((1 - price / original_price) * 100, 1)
        
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
        elif any("mens" in t for t in tags_lower):
            gender = "men"
        elif any("womens" in t for t in tags_lower):
            gender = "women"
        
        category = "footwear" if "footwear" in collection else "apparel"
        
        return DealItem(
            source="kith",
            external_id=f"{product_id}_kith",
            title=title,
            brand=vendor,
            model=handle,
            category=category,
            gender=gender,
            price=price,
            original_price=original_price,
            discount_percent=discount_pct,
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
    """Scrape toutes les collections KITH."""
    results = {"collections": {}, "total_saved": 0, "total_skipped": 0}
    
    for collection in KITH_COLLECTIONS:
        result = collect_kith_collection(collection, min_score=min_score)
        results["collections"][collection] = result
        results["total_saved"] += result.get("deals_saved", 0)
        results["total_skipped"] += result.get("deals_skipped", 0)
    
    logger.info(f"KITH total: {results['total_saved']} saved, {results['total_skipped']} skipped")
    return results
