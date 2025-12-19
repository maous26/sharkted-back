"""
Premium Gate - Contrôle d'accès aux ressources coûteuses (Web Unlocker)

Règle d'or:
Web Unlocker n'est jamais utilisé "pour savoir".
Il est utilisé "parce que quelqu'un paie pour savoir".

Une requête Web Unlocker est autorisée uniquement si:
1. Au moins 1 utilisateur Premium actif existe
2. Le site est un site premium (Nike, Adidas, etc.)
3. Le produit a un score élevé OU une alerte Premium existe
4. Le scraping cheap a échoué

Chaque requête est traçable pour attribution business.
"""

import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Set
from dataclasses import dataclass, field
from enum import Enum
from loguru import logger

# Import database session (à adapter selon ton setup)
# from app.database import get_db
# from app.models import User


# =============================================================================
# CONFIGURATION
# =============================================================================

# Sites premium (nécessitent Web Unlocker)
PREMIUM_SITES = {
    "nike", "adidas", "zalando", "footlocker",
    "snkrs", "endclothing", "ssense", "mrporter"
}

# Seuil de score pour déclencher Web Unlocker
SCORE_THRESHOLD_HIGH = 70

# Coût estimé par requête Web Unlocker (en euros)
WEB_UNLOCKER_COST_PER_REQUEST = 0.002

# Plans considérés comme Premium
PREMIUM_PLANS = {"premium", "pro", "agency", "owner"}

# Quota par défaut de requêtes Web Unlocker par jour par Premium
DEFAULT_DAILY_QUOTA = 100


# =============================================================================
# DATA CLASSES
# =============================================================================

class TriggerType(str, Enum):
    """Type de déclencheur pour une requête Web Unlocker."""
    PREMIUM_ALERT = "premium_alert"      # Alerte personnalisée d'un Premium
    HIGH_SCORE = "high_score"            # Produit à score élevé
    CHEAP_FAILED = "cheap_failed"        # Fallback après échec proxy cheap
    MANUAL = "manual"                    # Déclenchement manuel admin


@dataclass
class WebUnlockerRequest:
    """Trace d'une requête Web Unlocker pour attribution business."""
    request_id: str
    timestamp: datetime
    source: str = "web_unlocker"
    cost_estimate: float = WEB_UNLOCKER_COST_PER_REQUEST
    trigger: TriggerType = TriggerType.CHEAP_FAILED
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    site: str = "unknown"
    url: str = ""
    served_users: List[int] = field(default_factory=list)  # IDs des users Premium servis
    success: bool = False
    response_time_ms: float = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "cost_estimate": self.cost_estimate,
            "trigger": self.trigger.value,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "site": self.site,
            "url": self.url,
            "served_users_count": len(self.served_users),
            "served_users": self.served_users,
            "success": self.success,
            "response_time_ms": self.response_time_ms,
            "cost_per_user": self.cost_estimate / max(len(self.served_users), 1),
        }


@dataclass
class PremiumContext:
    """Contexte Premium pour une décision de scraping."""
    has_active_premium: bool = False
    premium_user_ids: List[int] = field(default_factory=list)
    alert_user_ids: List[int] = field(default_factory=list)  # Users avec alerte sur ce produit
    total_premium_count: int = 0
    
    @property
    def served_users(self) -> List[int]:
        """Users qui seront servis par cette requête."""
        if self.alert_user_ids:
            return self.alert_user_ids
        return self.premium_user_ids


# =============================================================================
# PREMIUM GATE - CONTRÔLEUR D'ACCÈS
# =============================================================================

class PremiumGate:
    """
    Contrôleur d'accès aux ressources Web Unlocker.
    
    Garantit que chaque requête coûteuse est justifiée par la valeur business.
    """
    
    def __init__(self):
        self._request_log: List[WebUnlockerRequest] = []
        self._daily_stats: Dict[str, Dict[str, Any]] = {}
        self._cache_premium_users: Optional[List[int]] = None
        self._cache_expiry: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)
    
    def should_use_web_unlocker(
        self,
        site: str,
        product_score: float = 0,
        has_premium_alert: bool = False,
        cheap_failed: bool = False,
        force: bool = False,
    ) -> tuple[bool, str]:
        """
        Décide si Web Unlocker doit être utilisé.
        
        Args:
            site: Slug du site (nike, adidas, etc.)
            product_score: Score du produit (0-100)
            has_premium_alert: Une alerte Premium existe pour ce produit
            cheap_failed: Le scraping cheap a échoué (403, 429, etc.)
            force: Forcer l'utilisation (admin only)
        
        Returns:
            (authorized, reason)
        """
        # Force = bypass (pour admin)
        if force:
            return True, "forced_by_admin"
        
        # Règle 1: Au moins 1 Premium actif
        context = self._get_premium_context()
        if not context.has_active_premium:
            return False, "no_active_premium_users"
        
        # Règle 2: Site premium
        if site.lower() not in PREMIUM_SITES:
            return False, f"site_{site}_not_premium"
        
        # Règle 3a: Alerte Premium explicite
        if has_premium_alert:
            return True, "premium_alert_exists"
        
        # Règle 3b: Score élevé
        if product_score >= SCORE_THRESHOLD_HIGH:
            return True, f"high_score_{product_score}"
        
        # Règle 4: Cheap a échoué et au moins une des conditions ci-dessus
        if cheap_failed and product_score >= 50:
            return True, "cheap_failed_moderate_score"
        
        return False, "conditions_not_met"
    
    def authorize_request(
        self,
        url: str,
        site: str,
        product_id: Optional[str] = None,
        product_name: Optional[str] = None,
        product_score: float = 0,
        trigger: TriggerType = TriggerType.CHEAP_FAILED,
    ) -> tuple[bool, Optional[WebUnlockerRequest]]:
        """
        Autorise et trace une requête Web Unlocker.
        
        Returns:
            (authorized, request_trace)
        """
        # Vérifier l'autorisation
        authorized, reason = self.should_use_web_unlocker(
            site=site,
            product_score=product_score,
            has_premium_alert=(trigger == TriggerType.PREMIUM_ALERT),
            cheap_failed=(trigger == TriggerType.CHEAP_FAILED),
        )
        
        if not authorized:
            logger.debug(f"Web Unlocker DENIED: {reason} for {url}")
            return False, None
        
        # Créer la trace
        context = self._get_premium_context()
        request_id = f"wu_{int(time.time()*1000)}_{site}"
        
        trace = WebUnlockerRequest(
            request_id=request_id,
            timestamp=datetime.utcnow(),
            trigger=trigger,
            product_id=product_id,
            product_name=product_name,
            site=site,
            url=url,
            served_users=context.served_users,
        )
        
        self._request_log.append(trace)
        self._update_daily_stats(trace)
        
        logger.info(
            f"Web Unlocker AUTHORIZED: {reason}",
            site=site,
            score=product_score,
            served_users=len(context.served_users),
        )
        
        return True, trace
    
    def record_result(
        self,
        request_id: str,
        success: bool,
        response_time_ms: float,
    ) -> None:
        """Enregistre le résultat d'une requête."""
        for req in reversed(self._request_log):
            if req.request_id == request_id:
                req.success = success
                req.response_time_ms = response_time_ms
                break
    
    def _get_premium_context(self) -> PremiumContext:
        """
        Récupère le contexte Premium (avec cache).
        
        TODO: Brancher sur la vraie DB pour compter les Premium actifs.
        """
        now = datetime.utcnow()
        
        # Check cache
        if self._cache_premium_users is not None and self._cache_expiry and now < self._cache_expiry:
            return PremiumContext(
                has_active_premium=len(self._cache_premium_users) > 0,
                premium_user_ids=self._cache_premium_users,
                total_premium_count=len(self._cache_premium_users),
            )
        
        # Simulé pour l'instant - À REMPLACER par vraie query DB
        # from app.database import get_db
        # from app.models import User
        # db = next(get_db())
        # premium_users = db.query(User).filter(
        #     User.plan.in_(PREMIUM_PLANS),
        #     User.is_active == True
        # ).all()
        # premium_ids = [u.id for u in premium_users]
        
        # TEMPORAIRE: Assume qu'il y a des Premium si le flag est set
        # À remplacer par la vraie logique
        premium_ids = self._fetch_premium_user_ids()
        
        self._cache_premium_users = premium_ids
        self._cache_expiry = now + self._cache_ttl
        
        return PremiumContext(
            has_active_premium=len(premium_ids) > 0,
            premium_user_ids=premium_ids,
            total_premium_count=len(premium_ids),
        )
    
    def _fetch_premium_user_ids(self) -> List[int]:
        """
        Fetch les IDs des utilisateurs Premium actifs.
        
        TODO: Implémenter la vraie query DB.
        """
        try:
            from app.db.session import SessionLocal
            from app.models.user import User
            
            db = SessionLocal()
            try:
                premium_users = db.query(User.id).filter(
                    User.plan.in_(list(PREMIUM_PLANS)),
                    User.is_active == True
                ).all()
                return [u[0] for u in premium_users]
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not fetch premium users: {e}")
            return []
    
    def _update_daily_stats(self, trace: WebUnlockerRequest) -> None:
        """Met à jour les stats journalières."""
        date_key = trace.timestamp.strftime("%Y-%m-%d")
        
        if date_key not in self._daily_stats:
            self._daily_stats[date_key] = {
                "total_requests": 0,
                "total_cost": 0.0,
                "by_site": {},
                "by_trigger": {},
                "unique_users_served": set(),
            }
        
        stats = self._daily_stats[date_key]
        stats["total_requests"] += 1
        stats["total_cost"] += trace.cost_estimate
        
        # Par site
        if trace.site not in stats["by_site"]:
            stats["by_site"][trace.site] = 0
        stats["by_site"][trace.site] += 1
        
        # Par trigger
        trigger_key = trace.trigger.value
        if trigger_key not in stats["by_trigger"]:
            stats["by_trigger"][trigger_key] = 0
        stats["by_trigger"][trigger_key] += 1
        
        # Users servis
        for user_id in trace.served_users:
            stats["unique_users_served"].add(user_id)
    
    def get_daily_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Retourne les stats d'une journée."""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        stats = self._daily_stats.get(date, {
            "total_requests": 0,
            "total_cost": 0.0,
            "by_site": {},
            "by_trigger": {},
            "unique_users_served": set(),
        })
        
        # Convert set to count for JSON serialization
        return {
            "date": date,
            "total_requests": stats["total_requests"],
            "total_cost_eur": round(stats["total_cost"], 4),
            "by_site": stats["by_site"],
            "by_trigger": stats["by_trigger"],
            "unique_users_served": len(stats["unique_users_served"]),
            "avg_cost_per_user": round(
                stats["total_cost"] / max(len(stats["unique_users_served"]), 1), 4
            ),
        }
    
    def get_recent_requests(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retourne les requêtes récentes."""
        return [req.to_dict() for req in self._request_log[-limit:]]


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

premium_gate = PremiumGate()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def should_use_web_unlocker(
    site: str,
    product_score: float = 0,
    has_premium_alert: bool = False,
    cheap_failed: bool = False,
) -> tuple[bool, str]:
    """Wrapper function pour vérifier l'autorisation."""
    return premium_gate.should_use_web_unlocker(
        site=site,
        product_score=product_score,
        has_premium_alert=has_premium_alert,
        cheap_failed=cheap_failed,
    )


def authorize_web_unlocker_request(
    url: str,
    site: str,
    product_id: Optional[str] = None,
    product_name: Optional[str] = None,
    product_score: float = 0,
    trigger: TriggerType = TriggerType.CHEAP_FAILED,
) -> tuple[bool, Optional[WebUnlockerRequest]]:
    """Wrapper function pour autoriser une requête."""
    return premium_gate.authorize_request(
        url=url,
        site=site,
        product_id=product_id,
        product_name=product_name,
        product_score=product_score,
        trigger=trigger,
    )


def get_web_unlocker_stats(date: Optional[str] = None) -> Dict[str, Any]:
    """Retourne les stats Web Unlocker."""
    return premium_gate.get_daily_stats(date)
