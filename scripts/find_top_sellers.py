"""
Script pour trouver les TOP vendeurs Vinted.
Utilise BrightData Web Unlocker pour bypass les protections.
"""
import httpx
import json
import time
import random
import re
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import Counter

# BrightData Web Unlocker Configuration
WEB_UNLOCKER_HOST = "brd.superproxy.io"
WEB_UNLOCKER_PORT = 33335
WEB_UNLOCKER_USER = "brd-customer-hl_cb216abc-zone-web_unlocker1"
WEB_UNLOCKER_PASS = "f3builbiy0xl"
PROXY_URL = f"http://{WEB_UNLOCKER_USER}:{WEB_UNLOCKER_PASS}@{WEB_UNLOCKER_HOST}:{WEB_UNLOCKER_PORT}"

# Marques liquides
LIQUID_BRANDS = {
    "nike", "adidas", "jordan", "the north face", "north face", "tnf",
    "carhartt", "stussy", "supreme", "palace", "stone island",
    "moncler", "canada goose", "patagonia", "ralph lauren", "tommy hilfiger",
    "lacoste", "calvin klein", "levis", "diesel", "new balance", "asics",
    "salomon", "fear of god", "essentials", "off-white", "yeezy",
    "vans", "converse", "puma", "reebok", "hoka", "cp company",
    "hugo boss", "burberry", "kenzo", "acne studios",
}


@dataclass
class SellerScore:
    username: str
    user_id: int
    profile_url: str
    total_sales: int = 0
    total_items: int = 0
    followers: int = 0
    rotation_score: float = 0
    dominant_brand: str = ""
    has_liquid_brands: bool = False
    liquid_brand_ratio: float = 0
    catalog_score: float = 0
    avg_price: float = 0
    round_price_ratio: float = 0
    pricing_score: float = 0
    format_score: float = 0
    reactivity_score: float = 0
    total_score: float = 0
    tier: str = ""


def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.vinted.fr/",
    }


def create_client():
    return httpx.Client(
        proxy=PROXY_URL,
        timeout=60,
        verify=False,
        follow_redirects=True,
    )


def search_sellers_web(client, search_term: str, limit: int = 30) -> List[Dict]:
    sellers = []
    # Use the HTML catalog page instead of API
    url = f"https://www.vinted.fr/catalog?search_text={search_term}&order=newest_first"

    try:
        headers = get_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        resp = client.get(url, headers=headers)

        if resp.status_code == 200:
            html = resp.text
            seen = set()

            # Extract user IDs from member links
            member_matches = re.findall(r'/member/(\d+)', html)
            for user_id_str in member_matches[:limit * 2]:
                user_id = int(user_id_str)
                if user_id not in seen:
                    seen.add(user_id)
                    # Try to find username near the ID
                    username_match = re.search(rf'member/{user_id}[^"]*"[^>]*>([^<]+)', html)
                    username = username_match.group(1).strip() if username_match else ""
                    sellers.append({
                        "user_id": user_id,
                        "username": username,
                        "profile_url": f"https://www.vinted.fr/member/{user_id}",
                    })
                    if len(sellers) >= limit:
                        break

            # Also try to extract from JSON in page
            if len(sellers) < 5:
                json_match = re.search(r'"catalogItems"\s*:\s*(\[.*?\])\s*,', html, re.DOTALL)
                if json_match:
                    try:
                        items = json.loads(json_match.group(1))
                        for item in items:
                            user = item.get("user", {})
                            user_id = user.get("id")
                            if user_id and user_id not in seen:
                                seen.add(user_id)
                                sellers.append({
                                    "user_id": user_id,
                                    "username": user.get("login", ""),
                                    "profile_url": f"https://www.vinted.fr/member/{user_id}",
                                })
                    except:
                        pass

            print(f"  Found {len(sellers)} sellers for '{search_term}'")
        else:
            print(f"  HTTP {resp.status_code} for '{search_term}'")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {str(e)[:80]}")

    return sellers


def get_seller_info_web(client, user_id: int) -> Optional[Dict]:
    # Use HTML member page instead of API
    url = f"https://www.vinted.fr/member/{user_id}"

    try:
        headers = get_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        resp = client.get(url, headers=headers)

        if resp.status_code == 200:
            html = resp.text
            info = {
                "user_id": user_id,
                "username": "",
                "total_sales": 0,
                "total_items": 0,
                "followers": 0,
            }

            # Extract from JSON in page
            login_match = re.search(r'"login"\s*:\s*"([^"]+)"', html)
            if login_match:
                info["username"] = login_match.group(1)

            items_match = re.search(r'"item_count"\s*:\s*(\d+)', html)
            if items_match:
                info["total_items"] = int(items_match.group(1))

            followers_match = re.search(r'"followers_count"\s*:\s*(\d+)', html)
            if followers_match:
                info["followers"] = int(followers_match.group(1))

            feedback_match = re.search(r'"positive_feedback_count"\s*:\s*(\d+)', html)
            if feedback_match:
                info["total_sales"] = int(feedback_match.group(1))

            return info
        else:
            print(f"    Profile HTTP {resp.status_code}")
    except Exception as e:
        print(f"  Profile error: {type(e).__name__}")

    return None


def get_seller_items_web(client, user_id: int, limit: int = 50) -> List[Dict]:
    items = []
    # Use HTML items page
    url = f"https://www.vinted.fr/member/{user_id}/items"

    try:
        headers = get_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        resp = client.get(url, headers=headers)

        if resp.status_code == 200:
            html = resp.text

            # Try to find items JSON in page
            items_match = re.search(r'"items"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
            if items_match:
                try:
                    raw_items = json.loads(items_match.group(1))
                    for item in raw_items[:limit]:
                        price_data = item.get("price", {})
                        if isinstance(price_data, dict):
                            price = float(price_data.get("amount", 0) or 0)
                        elif isinstance(price_data, str):
                            price = float(price_data.replace(",", "."))
                        else:
                            price = float(price_data or 0)

                        brand = item.get("brand", "") or ""
                        title = item.get("title", "") or ""

                        items.append({
                            "title": title,
                            "price": price,
                            "brand": brand,
                        })
                except json.JSONDecodeError:
                    pass

            # Fallback: extract prices from text
            if not items:
                price_matches = re.findall(r'(\d+(?:,\d+)?)\s*(?:EUR|€)', html)
                for price_str in price_matches[:limit]:
                    items.append({
                        "title": "",
                        "price": float(price_str.replace(",", ".")),
                        "brand": "",
                    })

    except Exception as e:
        print(f"  Items error: {type(e).__name__}")

    return items


def analyze_catalog(items: List[Dict]) -> Dict:
    brands = []
    liquid_count = 0

    for item in items:
        brand = item.get("brand", "") or ""
        title = item.get("title", "") or ""
        brand_text = (brand + " " + title).lower()

        for liquid in LIQUID_BRANDS:
            if liquid in brand_text:
                brands.append(liquid)
                liquid_count += 1
                break
        else:
            brands.append(brand_text[:20])

    brand_counts = Counter(brands)
    dominant = brand_counts.most_common(1)
    dominant_brand = dominant[0][0] if dominant else ""
    liquid_ratio = liquid_count / len(items) if items else 0

    return {
        "dominant_brand": dominant_brand,
        "liquid_brand_ratio": liquid_ratio,
        "has_liquid_brands": liquid_count > 0,
    }


def analyze_pricing(items: List[Dict]) -> Dict:
    prices = []
    round_prices = 0

    for item in items:
        price = item.get("price", 0)
        if isinstance(price, str):
            try:
                price = float(price.replace(",", "."))
            except:
                price = 0
        if price and price > 0:
            prices.append(price)
            if price % 10 == 0 or price % 5 == 0:
                round_prices += 1

    avg_price = sum(prices) / len(prices) if prices else 0
    round_ratio = round_prices / len(prices) if prices else 0

    return {"avg_price": avg_price, "round_price_ratio": round_ratio}


def calculate_scores(seller: SellerScore) -> SellerScore:
    # Rotation (0-25) - based on total sales
    if seller.total_sales >= 100:
        seller.rotation_score = 25
    elif seller.total_sales >= 50:
        seller.rotation_score = 20
    elif seller.total_sales >= 20:
        seller.rotation_score = 15
    elif seller.total_sales >= 10:
        seller.rotation_score = 10
    elif seller.total_sales >= 5:
        seller.rotation_score = 5
    else:
        seller.rotation_score = 0

    # Catalog (0-25)
    catalog_score = 0
    if seller.liquid_brand_ratio >= 0.5:
        catalog_score += 15
    elif seller.liquid_brand_ratio >= 0.3:
        catalog_score += 10
    elif seller.has_liquid_brands:
        catalog_score += 5

    if seller.total_items >= 50:
        catalog_score += 10
    elif seller.total_items >= 20:
        catalog_score += 7
    elif seller.total_items >= 10:
        catalog_score += 4
    seller.catalog_score = min(catalog_score, 25)

    # Pricing (0-20)
    pricing_score = 0
    if seller.round_price_ratio < 0.3:
        pricing_score += 15
    elif seller.round_price_ratio < 0.5:
        pricing_score += 10
    elif seller.round_price_ratio < 0.7:
        pricing_score += 5
    if 20 <= seller.avg_price <= 150:
        pricing_score += 5
    seller.pricing_score = min(pricing_score, 20)

    # Format (0-15)
    if seller.total_items >= 30:
        seller.format_score = 15
    elif seller.total_items >= 15:
        seller.format_score = 10
    elif seller.total_items >= 8:
        seller.format_score = 5
    else:
        seller.format_score = 0

    # Reactivity (0-15)
    if seller.total_sales >= 50:
        seller.reactivity_score = 15
    elif seller.total_sales >= 20:
        seller.reactivity_score = 12
    else:
        seller.reactivity_score = 10

    # Total
    seller.total_score = (
        seller.rotation_score + seller.catalog_score +
        seller.pricing_score + seller.format_score + seller.reactivity_score
    )

    if seller.total_score >= 70:
        seller.tier = "S"
    elif seller.total_score >= 55:
        seller.tier = "A"
    elif seller.total_score >= 40:
        seller.tier = "B"
    else:
        seller.tier = "C"

    return seller


def analyze_seller(client, user_id: int, username: str) -> Optional[SellerScore]:
    print(f"  Analyzing user {user_id}...")

    info = get_seller_info_web(client, user_id)
    if not info:
        return None

    time.sleep(random.uniform(1, 2))

    items = get_seller_items_web(client, user_id, limit=50)

    seller = SellerScore(
        username=info.get("username") or username or str(user_id),
        user_id=user_id,
        profile_url=f"https://www.vinted.fr/member/{user_id}",
        total_sales=info.get("total_sales", 0),
        total_items=info.get("total_items", len(items)),
        followers=info.get("followers", 0),
    )

    if items:
        catalog = analyze_catalog(items)
        seller.dominant_brand = catalog["dominant_brand"]
        seller.liquid_brand_ratio = catalog["liquid_brand_ratio"]
        seller.has_liquid_brands = catalog["has_liquid_brands"]

        pricing = analyze_pricing(items)
        seller.avg_price = pricing["avg_price"]
        seller.round_price_ratio = pricing["round_price_ratio"]

    seller = calculate_scores(seller)

    print(f"    -> @{seller.username}: Score {seller.total_score}/100 (Tier {seller.tier}) - {seller.total_sales} ventes")
    return seller


def main():
    print("=" * 60)
    print("RECHERCHE DES TOP VENDEURS VINTED")
    print("=" * 60)
    print(f"Using Web Unlocker: {WEB_UNLOCKER_HOST}:{WEB_UNLOCKER_PORT}")

    client = create_client()

    all_sellers = {}

    searches = [
        "nike sneakers", "adidas sneakers", "jordan sneakers",
        "the north face doudoune", "carhartt hoodie",
        "nike hoodie", "adidas survetement",
        "ralph lauren polo", "new balance sneakers",
        "stussy t-shirt", "lacoste polo", "supreme sweat",
    ]

    print("\n[1/3] Recherche des vendeurs...\n")

    for search_term in searches:
        sellers = search_sellers_web(client, search_term, limit=20)
        for s in sellers:
            if s["user_id"] not in all_sellers:
                all_sellers[s["user_id"]] = s
        time.sleep(random.uniform(1, 2))

    print(f"\nTotal: {len(all_sellers)} vendeurs uniques")

    print("\n[2/3] Analyse detaillee...\n")

    analyzed = []
    for i, (user_id, data) in enumerate(list(all_sellers.items())[:60]):
        seller = analyze_seller(client, user_id, data.get("username", ""))
        if seller and seller.total_score >= 35:
            analyzed.append(seller)
        if len(analyzed) >= 35:
            break
        time.sleep(random.uniform(1, 2))

    analyzed.sort(key=lambda x: x.total_score, reverse=True)
    top_sellers = analyzed[:30]

    print("\n" + "=" * 60)
    print("[3/3] TOP 30 VENDEURS")
    print("=" * 60)

    results = []
    for i, seller in enumerate(top_sellers, 1):
        result = {
            "rank": i,
            "username": seller.username,
            "user_id": seller.user_id,
            "profile_url": seller.profile_url,
            "tier": seller.tier,
            "total_score": seller.total_score,
            "scores": {
                "rotation": seller.rotation_score,
                "catalog": seller.catalog_score,
                "pricing": seller.pricing_score,
                "format": seller.format_score,
                "reactivity": seller.reactivity_score,
            },
            "metrics": {
                "total_sales": seller.total_sales,
                "total_items": seller.total_items,
                "followers": seller.followers,
                "dominant_brand": seller.dominant_brand,
                "liquid_brand_ratio": round(seller.liquid_brand_ratio * 100, 1),
                "avg_price": round(seller.avg_price, 2),
            },
        }
        results.append(result)

        print(f"\n#{i} [{seller.tier}] @{seller.username}")
        print(f"   Score: {seller.total_score}/100")
        print(f"   URL: {seller.profile_url}")
        print(f"   Ventes: {seller.total_sales} | Items: {seller.total_items} | Followers: {seller.followers}")
        print(f"   Marque dominante: {seller.dominant_brand}")
        print(f"   Marques liquides: {seller.liquid_brand_ratio*100:.0f}%")
        print(f"   Prix moyen: {seller.avg_price:.0f} EUR")

    # Save results
    output_file = "top_sellers.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n\nResultats sauvegardes dans {output_file}")
    tier_s = len([s for s in top_sellers if s.tier == "S"])
    tier_a = len([s for s in top_sellers if s.tier == "A"])
    tier_b = len([s for s in top_sellers if s.tier == "B"])
    print(f"Tier S: {tier_s} | Tier A: {tier_a} | Tier B: {tier_b}")

    return results


if __name__ == "__main__":
    main()
