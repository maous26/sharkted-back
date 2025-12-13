from datetime import datetime
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.deal import Deal
from app.normalizers.item import DealItem


class DealRepository:
    """
    Repository pour la persistance des deals.
    Gère l'upsert (insert ou update) basé sur la clé logique (source, external_id).
    """

    def __init__(self, session: Session):
        self.session = session

    def upsert(self, item: DealItem) -> Deal:
        """
        Insert ou update un deal.

        - Si le deal n'existe pas: insert
        - Si le deal existe: update last_seen_at et prix si changé

        Returns: Le deal persisté
        """
        now = datetime.utcnow()

        # Chercher un deal existant
        existing = self.session.query(Deal).filter(
            Deal.source == item.source,
            Deal.external_id == item.external_id
        ).first()

        if existing:
            # Update
            existing.last_seen_at = now
            existing.title = item.title
            existing.url = item.url
            existing.image_url = item.image_url
            existing.seller_name = item.seller_name
            existing.location = item.location
            existing.raw_data = item.raw

            # Track changement de prix
            if existing.price != item.price:
                existing.original_price = existing.price
                existing.price = item.price
                existing.price_updated_at = now

            self.session.flush()
            return existing
        else:
            # Insert
            deal = Deal(
                source=item.source,
                external_id=item.external_id,
                title=item.title,
                price=item.price,
                currency=item.currency,
                url=item.url,
                image_url=item.image_url,
                seller_name=item.seller_name,
                location=item.location,
                raw_data=item.raw,
                first_seen_at=now,
                last_seen_at=now,
                in_stock=True,
            )
            self.session.add(deal)
            self.session.flush()
            return deal

    def upsert_batch(self, items: List[DealItem]) -> List[Deal]:
        """
        Upsert une liste de deals.
        """
        return [self.upsert(item) for item in items]

    def get_by_source_and_id(self, source: str, external_id: str) -> Optional[Deal]:
        """Récupère un deal par sa clé logique."""
        return self.session.query(Deal).filter(
            Deal.source == source,
            Deal.external_id == external_id
        ).first()

    def get_by_source(
        self,
        source: str,
        limit: int = 100,
        offset: int = 0,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        in_stock_only: bool = True,
    ) -> List[Deal]:
        """Récupère les deals d'une source avec filtres."""
        query = self.session.query(Deal).filter(Deal.source == source)

        if in_stock_only:
            query = query.filter(Deal.in_stock == True)
        if min_price is not None:
            query = query.filter(Deal.price >= min_price)
        if max_price is not None:
            query = query.filter(Deal.price <= max_price)

        return query.order_by(Deal.last_seen_at.desc()).offset(offset).limit(limit).all()

    def get_recent(self, hours: int = 24, limit: int = 100) -> List[Deal]:
        """Récupère les deals vus récemment."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return (
            self.session.query(Deal)
            .filter(Deal.last_seen_at >= cutoff)
            .order_by(Deal.last_seen_at.desc())
            .limit(limit)
            .all()
        )

    def mark_out_of_stock(self, source: str, external_id: str) -> bool:
        """Marque un deal comme indisponible."""
        deal = self.get_by_source_and_id(source, external_id)
        if deal:
            deal.in_stock = False
            self.session.flush()
            return True
        return False

    def count_by_source(self, source: str) -> int:
        """Compte les deals d'une source."""
        return self.session.query(Deal).filter(Deal.source == source).count()


# Import manquant
from datetime import timedelta
