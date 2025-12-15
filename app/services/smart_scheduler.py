"""
Smart Scheduler - Scraping en couches avec fréquences dynamiques.

Stratégie en 3 couches:
1. SEED (haute fréquence): /soldes, /promos, /outlet - découverte rapide
2. CATEGORY (moyenne fréquence): catégories standards - couverture large  
3. WATCHLIST (très haute fréquence): produits suivis - détection de drops

Ajustement dynamique:
- Source avec beaucoup de nouveautés → fréquence ↑
- Source stable/chère → fréquence ↓
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.services.price_tracking_service import get_deals_to_watch

logger = get_logger(__name__)


class ScrapeLayer(str, Enum):
    """Couches de scraping."""
    SEED = "seed"           # Promos/soldes - haute fréquence
    CATEGORY = "category"   # Catégories standards - moyenne fréquence
    WATCHLIST = "watchlist" # Produits suivis - très haute fréquence


@dataclass
class LayerConfig:
    """Configuration d'une couche de scraping."""
    layer: ScrapeLayer
    interval_minutes: int
    max_products: int
    priority: int  # 1 = plus haute priorité


@dataclass
class SourceSchedule:
    """Planning de scraping pour une source."""
    source: str
    layers: Dict[ScrapeLayer, LayerConfig]
    last_scrape: Dict[ScrapeLayer, Optional[datetime]] = field(default_factory=dict)
    success_rate: float = 1.0  # Pour ajustement dynamique
    avg_new_products: float = 0.0  # Moyenne de nouveaux produits par scrape


# Configuration par défaut des couches
DEFAULT_LAYER_CONFIGS = {
    ScrapeLayer.SEED: LayerConfig(
        layer=ScrapeLayer.SEED,
        interval_minutes=15,  # Toutes les 15 min
        max_products=50,
        priority=1,
    ),
    ScrapeLayer.CATEGORY: LayerConfig(
        layer=ScrapeLayer.CATEGORY,
        interval_minutes=60,  # Toutes les heures
        max_products=30,
        priority=2,
    ),
    ScrapeLayer.WATCHLIST: LayerConfig(
        layer=ScrapeLayer.WATCHLIST,
        interval_minutes=10,  # Toutes les 10 min
        max_products=20,
        priority=1,
    ),
}


# URLs par source et par couche
SOURCE_LAYER_URLS: Dict[str, Dict[ScrapeLayer, List[str]]] = {
    "jdsports": {
        ScrapeLayer.SEED: [
            "https://www.jdsports.fr/promo/",
            "https://www.jdsports.fr/homme/chaussures-homme/promo/",
            "https://www.jdsports.fr/femme/chaussures-femme/promo/",
        ],
        ScrapeLayer.CATEGORY: [
            "https://www.jdsports.fr/homme/chaussures-homme/baskets/",
            "https://www.jdsports.fr/femme/chaussures-femme/baskets/",
            "https://www.jdsports.fr/homme/chaussures-homme/?sort=newest",
        ],
    },
    "size": {
        ScrapeLayer.SEED: [
            "https://www.size.co.uk/sale/",
            "https://www.size.co.uk/mens/footwear/sale/",
            "https://www.size.co.uk/womens/footwear/sale/",
        ],
        ScrapeLayer.CATEGORY: [
            "https://www.size.co.uk/mens/footwear/",
            "https://www.size.co.uk/womens/footwear/",
            "https://www.size.co.uk/search/?q=&sort=latest",
        ],
    },
    "courir": {
        ScrapeLayer.SEED: [
            "https://www.courir.com/fr/c/promotions-en-cours/",
            "https://www.courir.com/fr/c/promotions/",
        ],
        ScrapeLayer.CATEGORY: [
            "https://www.courir.com/fr/c/homme/chaussures/",
            "https://www.courir.com/fr/c/femme/chaussures/",
        ],
    },
    "footlocker": {
        ScrapeLayer.SEED: [
            "https://www.footlocker.fr/fr/category/soldes.html",
        ],
        ScrapeLayer.CATEGORY: [
            "https://www.footlocker.fr/category/hommes/chaussures.html",
            "https://www.footlocker.fr/category/femmes/chaussures.html",
        ],
    },
}


class SmartScheduler:
    """Gestionnaire de scheduling intelligent."""
    
    def __init__(self):
        self.schedules: Dict[str, SourceSchedule] = {}
        self._init_schedules()
    
    def _init_schedules(self):
        """Initialise les schedules pour chaque source."""
        for source in SOURCE_LAYER_URLS:
            self.schedules[source] = SourceSchedule(
                source=source,
                layers=DEFAULT_LAYER_CONFIGS.copy(),
                last_scrape={layer: None for layer in ScrapeLayer},
            )
    
    def get_next_jobs(self, max_jobs: int = 5) -> List[Dict]:
        """
        Retourne les prochains jobs à exécuter.
        
        Priorise:
        1. Watchlist (détection de drops)
        2. Seed (découverte de promos)
        3. Category (couverture)
        """
        now = datetime.utcnow()
        jobs = []
        
        # 1. Jobs watchlist
        watchlist_deals = get_deals_to_watch(limit=50)
        if watchlist_deals:
            jobs.append({
                "type": "watchlist",
                "layer": ScrapeLayer.WATCHLIST,
                "deal_ids": watchlist_deals[:20],
                "priority": 1,
            })
        
        # 2. Jobs par source et couche
        for source, schedule in self.schedules.items():
            for layer, config in schedule.layers.items():
                if layer == ScrapeLayer.WATCHLIST:
                    continue  # Géré séparément
                
                last = schedule.last_scrape.get(layer)
                if last is None or (now - last) >= timedelta(minutes=config.interval_minutes):
                    urls = SOURCE_LAYER_URLS.get(source, {}).get(layer, [])
                    if urls:
                        jobs.append({
                            "type": "scrape",
                            "source": source,
                            "layer": layer,
                            "urls": urls,
                            "max_products": config.max_products,
                            "priority": config.priority,
                        })
        
        # Trier par priorité et limiter
        jobs.sort(key=lambda x: x["priority"])
        return jobs[:max_jobs]
    
    def mark_completed(
        self,
        source: str,
        layer: ScrapeLayer,
        success: bool,
        new_products: int = 0,
    ):
        """
        Marque un job comme terminé et ajuste la fréquence.
        
        Ajustement dynamique:
        - Beaucoup de nouveaux produits → réduire l'intervalle
        - Peu de nouveaux produits → augmenter l'intervalle
        - Échecs répétés → augmenter l'intervalle
        """
        if source not in self.schedules:
            return
        
        schedule = self.schedules[source]
        schedule.last_scrape[layer] = datetime.utcnow()
        
        # Mise à jour du taux de succès (moyenne mobile)
        if success:
            schedule.success_rate = schedule.success_rate * 0.9 + 0.1
        else:
            schedule.success_rate = schedule.success_rate * 0.9
        
        # Mise à jour de la moyenne de nouveaux produits
        schedule.avg_new_products = schedule.avg_new_products * 0.8 + new_products * 0.2
        
        # Ajustement dynamique de l'intervalle
        config = schedule.layers[layer]
        base_interval = DEFAULT_LAYER_CONFIGS[layer].interval_minutes
        
        if schedule.success_rate < 0.5:
            # Beaucoup d'échecs → ralentir
            config.interval_minutes = min(base_interval * 2, 120)
        elif schedule.avg_new_products > 10:
            # Beaucoup de nouveautés → accélérer
            config.interval_minutes = max(base_interval // 2, 5)
        else:
            # Revenir progressivement à la normale
            config.interval_minutes = int(
                config.interval_minutes * 0.9 + base_interval * 0.1
            )
        
        logger.debug(
            f"Schedule updated",
            source=source,
            layer=layer.value,
            interval=config.interval_minutes,
            success_rate=schedule.success_rate,
            avg_new=schedule.avg_new_products,
        )
    
    def get_status(self) -> Dict:
        """Retourne le statut actuel du scheduler."""
        now = datetime.utcnow()
        status = {}
        
        for source, schedule in self.schedules.items():
            source_status = {
                "success_rate": round(schedule.success_rate, 2),
                "avg_new_products": round(schedule.avg_new_products, 1),
                "layers": {},
            }
            
            for layer, config in schedule.layers.items():
                last = schedule.last_scrape.get(layer)
                next_run = None
                if last:
                    next_run = last + timedelta(minutes=config.interval_minutes)
                
                source_status["layers"][layer.value] = {
                    "interval_minutes": config.interval_minutes,
                    "last_scrape": last.isoformat() if last else None,
                    "next_scrape": next_run.isoformat() if next_run else "pending",
                    "overdue": next_run and now > next_run if next_run else True,
                }
            
            status[source] = source_status
        
        return status


# Singleton
_scheduler = None

def get_scheduler() -> SmartScheduler:
    """Retourne l'instance du scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SmartScheduler()
    return _scheduler
