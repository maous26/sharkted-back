"""
Vinted Cache Service - Scraping batch avec cache Redis.

Stratégie:
1. Les deals sont collectés et stockés SANS score
2. Un job batch scrape Vinted toutes les 15 min (moins agressif)
3. Les stats Vinted sont cachées dans Redis
4. Le scoring utilise le cache (pas d'appel Vinted en temps réel)
5. Les deals sont affichés une fois scorés
"""

import json
import hashlib
import random
import time
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import statistics

import httpx
import redis
from loguru import logger

# Redis connection
REDIS_URL = "redis://redis:6379/0"
CACHE_TTL = 3600  # 1 heure de cache
VINTED_BATCH_INTERVAL = 900  # 15 minutes


def get_redis():
    return redis.from_url(REDIS_URL)


def get_cache_key(query: str) -> str:
    """Génère une clé de cache normalisée pour une requête."""
    normalized = query.lower().strip()
    normalized = re.sub(r'[^a-z0-9\s]', '', normalized)
    normalized = ' '.join(normalized.split())
    hash_key = hashlib.md5(normalized.encode()).hexdigest()[:12]
    return f"vinted:stats:{hash_key}"


def get_cached_stats(query: str) -> Optional[Dict]:
    """Récupère les stats Vinted depuis le cache."""
    try:
        r = get_redis()
        key = get_cache_key(query)
        data = r.get(key)
        if data:
            stats = json.loads(data)
            logger.debug(f"Cache HIT for '{query[:30]}'")
            return stats
        logger.debug(f"Cache MISS for '{query[:30]}'")
        return None
    except Exception as e:
        logger.warning(f"Redis cache error: {e}")
        return None


def set_cached_stats(query: str, stats: Dict, ttl: int = CACHE_TTL):
    """Stocke les stats Vinted dans le cache."""
    try:
        r = get_redis()
        key = get_cache_key(query)
        r.setex(key, ttl, json.dumps(stats))
        logger.debug(f"Cached stats for '{query[:30]}'")
    except Exception as e:
        logger.warning(f"Redis cache set error: {e}")


class VintedBatchScraper:
    """
    Scraper Vinted batch - conçu pour être exécuté périodiquement.
    Plus lent mais moins susceptible d'être bloqué.
    """
    
    BASE_URL = "https://www.vinted.fr"
    SEARCH_URL = f"{BASE_URL}/api/v2/catalog/items"
    
    USER_AGENTS = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    ]
    
    def __init__(self):
        self._cookies = None
        self._last_request = 0
        self._min_delay = 3  # 3 secondes entre chaque requête (moins agressif)
    
    def _get_headers(self, for_api: bool = False) -> Dict:
        headers = {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        if for_api:
            headers["Referer"] = self.BASE_URL
        return headers
    
    def _init_session(self) -> bool:
        """Initialise une session Vinted (récupère les cookies)."""
        try:
            # Attendre entre les requêtes
            elapsed = time.time() - self._last_request
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)
            
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(self.BASE_URL, headers=self._get_headers())
                self._last_request = time.time()
                
                if resp.status_code == 200:
                    self._cookies = dict(resp.cookies)
                    logger.info("Vinted session initialized")
                    return True
                else:
                    logger.warning(f"Vinted init failed: {resp.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Vinted session error: {e}")
            return False
    
    def search(self, query: str, limit: int = 20) -> Optional[Dict]:
        """
        Recherche sur Vinted et retourne les stats calculées.
        """
        # Vérifier le cache d'abord
        cached = get_cached_stats(query)
        if cached:
            return cached
        
        # Initialiser session si nécessaire
        if not self._cookies:
            if not self._init_session():
                return None
        
        # Attendre entre les requêtes
        elapsed = time.time() - self._last_request
        if elapsed < self._min_delay:
            time.sleep(self._min_delay - elapsed)
        
        try:
            params = {
                "search_text": query,
                "per_page": limit,
                "order": "newest_first",
            }
            
            headers = self._get_headers(for_api=True)
            
            with httpx.Client(timeout=30, cookies=self._cookies) as client:
                resp = client.get(self.SEARCH_URL, params=params, headers=headers)
                self._last_request = time.time()
                
                if resp.status_code == 403:
                    logger.warning("Vinted 403 - rate limited")
                    self._cookies = None  # Reset session
                    return None
                
                if resp.status_code != 200:
                    logger.warning(f"Vinted search failed: {resp.status_code}")
                    return None
                
                data = resp.json()
                items = data.get("items", [])
                
                if not items:
                    stats = {"nb_listings": 0, "query_used": query}
                    set_cached_stats(query, stats)
                    return stats
                
                # Calculer les stats
                stats = self._calculate_stats(items, query)
                
                # Mettre en cache
                set_cached_stats(query, stats)
                
                return stats
                
        except Exception as e:
            logger.error(f"Vinted search error: {e}")
            return None
    
    def _calculate_stats(self, items: List[Dict], query: str) -> Dict:
        """Calcule les statistiques à partir des items Vinted."""
        prices = []
        sample_listings = []
        
        for item in items[:20]:
            try:
                price = float(item.get("price", {}).get("amount", 0))
                if price > 0:
                    prices.append(price)
                
                if len(sample_listings) < 5:
                    sample_listings.append({
                        "title": item.get("title", "")[:50],
                        "price": price,
                        "url": item.get("url", ""),
                        "photo_url": item.get("photo", {}).get("url"),
                    })
            except:
                continue
        
        if not prices:
            return {"nb_listings": len(items), "query_used": query}
        
        prices.sort()
        n = len(prices)
        
        stats = {
            "nb_listings": len(items),
            "price_min": min(prices),
            "price_max": max(prices),
            "price_avg": round(statistics.mean(prices), 2),
            "price_median": round(statistics.median(prices), 2),
            "query_used": query,
            "sample_listings": sample_listings,
            "fetched_at": datetime.utcnow().isoformat(),
        }
        
        # Percentiles
        if n >= 4:
            stats["price_p25"] = round(prices[n // 4], 2)
            stats["price_p75"] = round(prices[3 * n // 4], 2)
        
        # Coefficient de variation
        if n >= 2:
            try:
                std = statistics.stdev(prices)
                stats["coefficient_variation"] = round(std / stats["price_avg"] * 100, 1)
            except:
                pass
        
        # Liquidité basée sur le nombre d'annonces
        if len(items) >= 15:
            stats["liquidity_score"] = 80
        elif len(items) >= 10:
            stats["liquidity_score"] = 60
        elif len(items) >= 5:
            stats["liquidity_score"] = 40
        else:
            stats["liquidity_score"] = 20
        
        return stats


# Instance globale
_scraper = None

def get_scraper() -> VintedBatchScraper:
    global _scraper
    if _scraper is None:
        _scraper = VintedBatchScraper()
    return _scraper


def get_vinted_stats_cached(product_name: str, brand: str = None, sale_price: float = None) -> Dict:
    """
    Récupère les stats Vinted depuis le cache OU les scrape si absent.
    
    Pour le scoring instantané, on utilise uniquement le cache.
    Le scraping batch alimente le cache périodiquement.
    """
    # Construire la query
    query = product_name[:40]
    if brand and brand.lower() not in query.lower():
        query = f"{brand} {query}"[:50]
    
    # Essayer le cache
    stats = get_cached_stats(query)
    
    if stats:
        # Calculer la marge si on a le prix de vente
        if sale_price and stats.get("price_median"):
            margin_euro = stats["price_median"] - sale_price
            margin_pct = (margin_euro / sale_price * 100) if sale_price > 0 else 0
            stats["margin_euro"] = round(margin_euro, 2)
            stats["margin_pct"] = round(margin_pct, 1)
        return stats
    
    # Pas de cache - retourner des stats vides
    # Le batch job alimentera le cache plus tard
    return {
        "nb_listings": 0,
        "query_used": query,
        "cache_miss": True,
    }


def scrape_vinted_for_query(query: str) -> Optional[Dict]:
    """Scrape Vinted pour une requête spécifique (utilisé par le batch job)."""
    scraper = get_scraper()
    return scraper.search(query)


def batch_scrape_pending_deals(limit: int = 50) -> Dict:
    """
    Job batch: scrape Vinted pour les deals sans stats.
    Exécuté toutes les 15 minutes.
    """
    from app.db.session import SessionLocal
    from app.models.deal import Deal
    from app.models.vinted_stats import VintedStats
    
    session = SessionLocal()
    scraper = get_scraper()
    
    try:
        # Trouver les deals sans stats Vinted
        deals_without_stats = session.query(Deal).outerjoin(
            VintedStats, Deal.id == VintedStats.deal_id
        ).filter(
            VintedStats.id == None
        ).order_by(Deal.first_seen_at.desc()).limit(limit).all()
        
        logger.info(f"Batch Vinted: {len(deals_without_stats)} deals to process")
        
        scraped = 0
        errors = 0
        
        for deal in deals_without_stats:
            try:
                # Construire la query
                query = deal.title[:40]
                if deal.brand and deal.brand.lower() not in query.lower():
                    query = f"{deal.brand} {query}"[:50]
                
                # Scraper (avec délai intégré)
                stats = scraper.search(query)
                
                if stats and stats.get("nb_listings", 0) > 0:
                    # Calculer la marge
                    if stats.get("price_median") and deal.price:
                        margin_euro = stats["price_median"] - deal.price
                        margin_pct = (margin_euro / deal.price * 100) if deal.price > 0 else 0
                        stats["margin_euro"] = round(margin_euro, 2)
                        stats["margin_pct"] = round(margin_pct, 1)
                    
                    # Sauvegarder en base
                    vinted_stats = VintedStats(
                        deal_id=deal.id,
                        nb_listings=stats.get("nb_listings", 0),
                        price_min=stats.get("price_min"),
                        price_max=stats.get("price_max"),
                        price_avg=stats.get("price_avg"),
                        price_median=stats.get("price_median"),
                        price_p25=stats.get("price_p25"),
                        price_p75=stats.get("price_p75"),
                        coefficient_variation=stats.get("coefficient_variation"),
                        margin_euro=stats.get("margin_euro"),
                        margin_pct=stats.get("margin_pct"),
                        liquidity_score=stats.get("liquidity_score"),
                        sample_listings=stats.get("sample_listings", []),
                        search_query=stats.get("query_used", ""),
                    )
                    session.add(vinted_stats)
                    session.commit()
                    scraped += 1
                    logger.info(f"Vinted stats saved for deal {deal.id}")
                else:
                    errors += 1
                    
            except Exception as e:
                errors += 1
                logger.warning(f"Batch Vinted error for deal {deal.id}: {e}")
                session.rollback()
                continue
        
        return {
            "status": "completed",
            "deals_processed": len(deals_without_stats),
            "stats_saved": scraped,
            "errors": errors,
        }
        
    finally:
        session.close()


def batch_rescore_deals(limit: int = 50) -> Dict:
    """
    Job batch: recalcule le score des deals qui ont maintenant des stats Vinted.
    Exécuté après le batch Vinted.
    """
    from app.db.session import SessionLocal
    from app.models.deal import Deal
    from app.models.vinted_stats import VintedStats
    from app.models.deal_score import DealScore
    from app.services.scoring_service import score_deal
    import asyncio
    
    session = SessionLocal()
    
    try:
        # Trouver les deals avec stats Vinted mais sans score OU score ancien
        subquery = session.query(DealScore.deal_id)
        
        deals_to_score = session.query(Deal, VintedStats).join(
            VintedStats, Deal.id == VintedStats.deal_id
        ).outerjoin(
            DealScore, Deal.id == DealScore.deal_id
        ).filter(
            DealScore.id == None  # Pas encore de score
        ).limit(limit).all()
        
        logger.info(f"Batch rescore: {len(deals_to_score)} deals to score")
        
        scored = 0
        deleted = 0
        
        for deal, vinted_stats in deals_to_score:
            try:
                # Préparer les données
                deal_data = {
                    'product_name': deal.title,
                    'brand': deal.brand or deal.seller_name,
                    'model': deal.model,
                    'category': deal.category or 'default',
                    'discount_percent': deal.discount_percent or 0,
                    'sizes_available': deal.sizes_available,
                }
                
                vinted_data = {
                    'nb_listings': vinted_stats.nb_listings,
                    'price_median': vinted_stats.price_median,
                    'price_avg': vinted_stats.price_avg,
                    'margin_euro': vinted_stats.margin_euro,
                    'margin_pct': vinted_stats.margin_pct,
                    'liquidity_score': vinted_stats.liquidity_score,
                }
                
                # Calculer le score
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                score_result = loop.run_until_complete(score_deal(deal_data, vinted_data))
                loop.close()
                
                flip_score = score_result.get('flip_score', 0)
                
                # Si score < 60, supprimer le deal
                if flip_score < 60:
                    session.delete(vinted_stats)
                    session.delete(deal)
                    session.commit()
                    deleted += 1
                    logger.info(f"Deal {deal.id} deleted (score {flip_score:.1f} < 60)")
                    continue
                
                # Sinon, sauvegarder le score
                deal_score = DealScore(
                    deal_id=deal.id,
                    flip_score=flip_score,
                    margin_score=score_result.get('margin_score'),
                    liquidity_score=score_result.get('liquidity_score'),
                    popularity_score=score_result.get('popularity_score'),
                    recommended_action=score_result.get('recommended_action'),
                    recommended_price=score_result.get('recommended_price'),
                    confidence=score_result.get('confidence'),
                    explanation=score_result.get('explanation'),
                    explanation_short=score_result.get('explanation_short'),
                    risks=score_result.get('risks', []),
                    score_breakdown=score_result.get('score_breakdown', {}),
                    model_version='v2_batch',
                )
                session.add(deal_score)
                session.commit()
                scored += 1
                logger.info(f"Deal {deal.id} scored: {flip_score:.1f}")
                
            except Exception as e:
                logger.warning(f"Batch score error for deal {deal.id}: {e}")
                session.rollback()
                continue
        
        return {
            "status": "completed",
            "deals_processed": len(deals_to_score),
            "deals_scored": scored,
            "deals_deleted": deleted,
        }
        
    finally:
        session.close()
