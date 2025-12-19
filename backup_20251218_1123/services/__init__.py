from app.services.deal_service import (
    persist_deal,
    persist_deals_batch,
    get_deal,
    get_deals_by_source,
    get_source_stats,
)

__all__ = [
    "persist_deal",
    "persist_deals_batch",
    "get_deal",
    "get_deals_by_source",
    "get_source_stats",
]
