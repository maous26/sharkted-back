"""
Scraper KITH EU - Shopify JSON API
Collections: footwear-sale, apparel-sale, kids-footwear-sale
"""

import httpx
from typing import Dict, Any, List, Optional
from loguru import logger
from datetime import datetime

from app.normalizers.item import DealItem
from app.services.deal_service import persist_deal


KITH_BASE_URL = "https://eu.kith.com"
KITH_COLLECTIONS = [
    "footwear-sale",
    "apparel-sale", 
    "kids-footwear-sale",
    "kids-apparel-sale",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def collect_kith_collection(collection: str = "footwear-sale", limit: int = 250) -> Dict[str, Any]:
    """Scrape une collection KITH EU."""
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
                logger.info(f"KITH {collection} page {page}: {len(products)} produits")
                
                if len(products) < 250:
                    break
                    
                page += 1
        
        # Process products
        deals_saved = 0
        for product in all_products:
            deal_item = parse_kith_product(product, collection)
            if deal_item:
                persist_deal(deal_item)
                deals_saved += 1
        
        return {
            "status": "success",
            "source": "kith",
            "collection": collection,
            "products_found": len(all_products),
            "deals_saved": deals_saved,
        }
        
    except Exception as e:
        logger.error(f"KITH {collection} error: {e}")
        return {"status": "error", "error": str(e), "collection": collection}


def parse_kith_product(product: Dict, collection: str) -> Optional[DealItem]:
    """Parse un produit KITH en format DealItem."""
    try:
        product_id = str(product.get("id", ""))
        title = product.get("title", "")
        vendor = product.get("vendor", "")
        handle = product.get("handle", "")
        
        # Get variants with stock
        variants = product.get("variants", [])
        available_variants = [v for v in variants if v.get("available")]
        
        if not available_variants:
            return None  # Skip out of stock
        
        # Get price info from first available variant
        first_variant = available_variants[0]
        price = float(first_variant.get("price", 0))
        compare_price = first_variant.get("compare_at_price")
        original_price = float(compare_price) if compare_price else None
        
        # Only keep if there's a discount
        if not original_price or original_price <= price:
            return None
        
        discount_pct = round((1 - price / original_price) * 100, 1) if original_price else 0
        
        # Get sizes as list of strings
        sizes = []
        for v in available_variants:
            size = v.get("option1") or v.get("title")
            if size and v.get("available") and size not in sizes:
                sizes.append(size)
        
        # Get image
        images = product.get("images", [])
        image_url = images[0].get("src") if images else None
        
        # Determine category and gender from collection/tags
        tags = product.get("tags", [])
        tags_lower = [t.lower() for t in tags]
        
        gender = "unisex"
        if "kids" in collection or any("kids" in t or "enfant" in t for t in tags_lower):
            gender = "kids"
        elif any("mens" in t or "men" == t for t in tags_lower):
            gender = "men"
        elif any("womens" in t or "women" == t for t in tags_lower):
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
            raw={"tags": tags[:10], "product_type": product.get("product_type")},
        )
        
    except Exception as e:
        logger.error(f"KITH parse error: {e}")
        return None


def collect_all_kith() -> Dict[str, Any]:
    """Scrape toutes les collections KITH."""
    results = {"collections": {}, "total_deals": 0}
    
    for collection in KITH_COLLECTIONS:
        result = collect_kith_collection(collection)
        results["collections"][collection] = result
        results["total_deals"] += result.get("deals_saved", 0)
    
    logger.info(f"KITH total: {results['total_deals']} deals saved")
    return results
