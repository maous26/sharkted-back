"""
ML Prediction Tracking - Stocke les prédictions pour feedback loop.

Cette table permet de :
1. Tracker chaque prédiction faite par le système
2. Comparer les prédictions aux résultats réels (via Outcomes)
3. Calculer l'accuracy par marque/catégorie/colorway
4. Nourrir les futures améliorations ML
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Boolean, JSON, Text
from sqlalchemy.orm import relationship

from app.models.user import Base


class MLPrediction(Base):
    """
    Stocke une prédiction de scoring pour analyse ML future.
    Liée à un deal et potentiellement à un outcome utilisateur.
    """
    __tablename__ = "ml_predictions"

    id = Column(Integer, primary_key=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True)

    # Timestamp
    predicted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Prédictions faites
    predicted_flip_score = Column(Float, nullable=False)
    predicted_sell_price = Column(Float, nullable=True)  # recommended_price
    predicted_sell_days = Column(Integer, nullable=True)  # estimated_sell_days
    predicted_margin_pct = Column(Float, nullable=True)
    predicted_margin_euro = Column(Float, nullable=True)
    predicted_action = Column(String(20), nullable=True)  # buy/watch/ignore

    # Contexte de la prédiction
    model_version = Column(String(50), nullable=False, default="hybrid_v1")
    scoring_source = Column(String(30), nullable=True)  # vinted_real, fallback_stats
    vinted_nb_listings = Column(Integer, nullable=True)  # Nb annonces Vinted trouvées
    vinted_price_median = Column(Float, nullable=True)

    # Détails produit (snapshot)
    brand = Column(String(100), nullable=True)
    category = Column(String(50), nullable=True)
    colorway = Column(String(100), nullable=True)
    color_category = Column(String(20), nullable=True)  # premium/neutral/risky
    sale_price = Column(Float, nullable=True)  # Prix d'achat
    original_price = Column(Float, nullable=True)
    discount_pct = Column(Float, nullable=True)

    # Résultats réels (remplis via Outcome) - FK désactivée pour le moment car table outcomes pas encore créée
    outcome_id = Column(Integer, nullable=True)  # ForeignKey("outcomes.id", ondelete="SET NULL") à activer plus tard
    actual_sold = Column(Boolean, nullable=True)  # L'utilisateur a-t-il vendu ?
    actual_sell_price = Column(Float, nullable=True)
    actual_sell_days = Column(Integer, nullable=True)
    actual_margin_pct = Column(Float, nullable=True)
    actual_margin_euro = Column(Float, nullable=True)

    # Feedback utilisateur
    user_rating = Column(Integer, nullable=True)  # 1-5, la prédiction était-elle bonne ?
    user_feedback = Column(Text, nullable=True)

    # Métriques calculées (pour analyse)
    price_error_pct = Column(Float, nullable=True)  # (actual - predicted) / predicted * 100
    days_error = Column(Integer, nullable=True)  # actual_days - predicted_days
    was_accurate = Column(Boolean, nullable=True)  # Prédiction considérée comme bonne ?

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relations
    deal = relationship("Deal", backref="ml_predictions")


class MLModelAccuracy(Base):
    """
    Agrégation des métriques d'accuracy par segment.
    Mise à jour périodiquement pour analyse.
    """
    __tablename__ = "ml_model_accuracy"

    id = Column(Integer, primary_key=True)

    # Segment analysé
    segment_type = Column(String(30), nullable=False)  # brand, category, colorway, color_category
    segment_value = Column(String(100), nullable=False)  # Nike, sneakers, Panda, premium
    model_version = Column(String(50), nullable=False)

    # Période
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    # Métriques
    total_predictions = Column(Integer, default=0)
    predictions_with_outcome = Column(Integer, default=0)  # Nb avec résultat réel

    # Accuracy prix
    avg_price_error_pct = Column(Float, nullable=True)
    median_price_error_pct = Column(Float, nullable=True)
    price_predictions_accurate = Column(Integer, default=0)  # Erreur < 10%

    # Accuracy délai
    avg_days_error = Column(Float, nullable=True)
    days_predictions_accurate = Column(Integer, default=0)  # Erreur < 5 jours

    # Accuracy recommandation
    buy_recommendations = Column(Integer, default=0)
    buy_actually_sold = Column(Integer, default=0)
    buy_with_profit = Column(Integer, default=0)

    # Score moyen
    avg_flip_score = Column(Float, nullable=True)
    avg_actual_margin_pct = Column(Float, nullable=True)

    # Coefficients suggérés (pour ajustement auto)
    suggested_price_adjustment = Column(Float, nullable=True)  # Ex: -0.05 = baisser de 5%
    suggested_days_adjustment = Column(Integer, nullable=True)

    # Timestamps
    computed_at = Column(DateTime, default=datetime.utcnow)

    class Config:
        # Index composite pour recherche rapide
        __table_args__ = (
            {"extend_existing": True},
        )


def record_prediction(
    session,
    deal_id: int,
    score_result: dict,
    deal_data: dict,
) -> MLPrediction:
    """
    Enregistre une prédiction pour tracking ML.
    Appelé après chaque scoring hybride.
    """
    prediction = MLPrediction(
        deal_id=deal_id,
        predicted_flip_score=score_result.get("flip_score", 0),
        predicted_sell_price=score_result.get("recommended_price"),
        predicted_sell_days=score_result.get("estimated_sell_days"),
        predicted_margin_pct=score_result.get("margin_pct"),
        predicted_margin_euro=score_result.get("margin_euro"),
        predicted_action=score_result.get("recommended_action"),
        model_version=score_result.get("model_version", "hybrid_v1"),
        scoring_source=score_result.get("vinted_source_type"),
        vinted_nb_listings=score_result.get("vinted_stats", {}).get("nb_listings") if score_result.get("vinted_stats") else None,
        vinted_price_median=score_result.get("vinted_stats", {}).get("price_median") if score_result.get("vinted_stats") else None,
        brand=deal_data.get("brand"),
        category=deal_data.get("category"),
        colorway=score_result.get("product_details", {}).get("colorway") if score_result.get("product_details") else None,
        color_category=score_result.get("product_details", {}).get("color_category") if score_result.get("product_details") else None,
        sale_price=deal_data.get("sale_price"),
        original_price=deal_data.get("original_price"),
        discount_pct=deal_data.get("discount_percent"),
    )

    session.add(prediction)
    return prediction


def update_prediction_with_outcome(
    session,
    prediction_id: int,
    outcome_id: int,
    actual_sell_price: float,
    actual_sell_days: int,
    actual_margin_pct: float,
    actual_margin_euro: float,
) -> Optional[MLPrediction]:
    """
    Met à jour une prédiction avec les résultats réels.
    Appelé quand un utilisateur marque un outcome comme vendu.
    """
    prediction = session.query(MLPrediction).filter(MLPrediction.id == prediction_id).first()
    if not prediction:
        return None

    prediction.outcome_id = outcome_id
    prediction.actual_sold = True
    prediction.actual_sell_price = actual_sell_price
    prediction.actual_sell_days = actual_sell_days
    prediction.actual_margin_pct = actual_margin_pct
    prediction.actual_margin_euro = actual_margin_euro

    # Calculer les erreurs
    if prediction.predicted_sell_price and actual_sell_price:
        prediction.price_error_pct = round(
            (actual_sell_price - prediction.predicted_sell_price) / prediction.predicted_sell_price * 100, 2
        )
        # Prédiction "accurate" si erreur < 15%
        prediction.was_accurate = abs(prediction.price_error_pct) < 15

    if prediction.predicted_sell_days and actual_sell_days:
        prediction.days_error = actual_sell_days - prediction.predicted_sell_days

    prediction.updated_at = datetime.utcnow()

    return prediction
