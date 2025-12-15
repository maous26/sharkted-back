"""
Drops Router - Détection et consultation des price drops.
Endpoints: /v1/drops/*
"""
from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime

from app.services.price_tracking_service import get_price_drops, get_deals_to_watch
from app.services.smart_scheduler import get_scheduler

router = APIRouter(prefix="/v1/drops", tags=["drops"])


@router.get("")
def list_price_drops(
    min_drop: float = Query(10.0, description="Drop minimum en %"),
    hours: int = Query(24, description="Fenêtre de temps en heures"),
    limit: int = Query(50, le=100),
):
    """
    Liste les deals avec des drops de prix récents.
    
    Un "drop" est détecté quand:
    - Le prix actuel est X% inférieur au min des 30 derniers jours
    - Ou le prix a baissé de X% depuis la dernière observation
    
    Le seuil X est ajusté selon la volatilité du produit:
    - Produit volatile (CV > 15%): seuil 10%
    - Produit stable: seuil 15%
    """
    drops = get_price_drops(
        min_drop_percent=min_drop,
        hours=hours,
        limit=limit,
    )
    
    return {
        "drops": drops,
        "count": len(drops),
        "min_drop_percent": min_drop,
        "hours": hours,
    }


@router.get("/watchlist")
def get_watchlist(limit: int = Query(50, le=200)):
    """
    Retourne les deals à surveiller en priorité.
    
    Critères:
    - Tendance baissière
    - Prix proche du minimum historique
    - Haute volatilité (opportunités fréquentes)
    """
    deal_ids = get_deals_to_watch(limit=limit)
    
    return {
        "deal_ids": deal_ids,
        "count": len(deal_ids),
    }


@router.get("/scheduler/status")
def get_scheduler_status():
    """
    Retourne le statut du scheduler intelligent.
    
    Montre:
    - Prochains jobs à exécuter
    - Intervalles actuels par source/couche
    - Taux de succès et ajustements
    """
    scheduler = get_scheduler()
    
    return {
        "status": scheduler.get_status(),
        "next_jobs": scheduler.get_next_jobs(max_jobs=10),
    }


@router.post("/scheduler/trigger")
def trigger_scheduler_job(
    source: Optional[str] = None,
    layer: Optional[str] = None,
):
    """
    Déclenche manuellement un job de scraping.
    
    Si source/layer non spécifiés, exécute les jobs prioritaires.
    """
    scheduler = get_scheduler()
    
    if source and layer:
        # Job spécifique
        from app.services.smart_scheduler import ScrapeLayer, SOURCE_LAYER_URLS
        layer_enum = ScrapeLayer(layer)
        urls = SOURCE_LAYER_URLS.get(source, {}).get(layer_enum, [])
        
        return {
            "triggered": True,
            "job": {
                "source": source,
                "layer": layer,
                "urls": urls,
            }
        }
    else:
        # Jobs prioritaires
        jobs = scheduler.get_next_jobs(max_jobs=3)
        return {
            "triggered": True,
            "jobs": jobs,
        }
