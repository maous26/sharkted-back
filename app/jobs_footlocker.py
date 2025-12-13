import time

from app.collectors.sources.footlocker import fetch_footlocker_product
from app.services.deal_service import persist_deal
from app.core.logging import get_logger, set_trace_id
from app.core.exceptions import CollectorError, BlockedError
from app.core.source_policy import (
    get_current_mode,
    get_policy,
    record_outcome,
    should_escalate,
    CollectMode,
)

logger = get_logger(__name__)

SOURCE = "footlocker"


def collect_footlocker_product(url: str) -> dict:
    """
    Job RQ avec support policy hybride:
    - vérifie le mode actuel (DIRECT/PROXY/BROWSER/BLOCKED)
    - appelle le collector
    - enregistre l'outcome pour les métriques
    - persiste le deal en base
    """
    trace_id = set_trace_id()
    start = time.perf_counter()

    # Vérifier si la source est bloquée
    current_mode = get_current_mode(SOURCE)
    policy = get_policy(SOURCE)

    if current_mode == CollectMode.BLOCKED or not policy.enabled:
        logger.warning(
            "Source is blocked or disabled",
            source=SOURCE,
            url=url,
            mode=current_mode.value,
            reason=policy.reason,
        )
        return {
            "trace_id": trace_id,
            "status": "skipped",
            "reason": f"Source {SOURCE} is {current_mode.value}",
            "duration_ms": 0,
        }

    logger.collect_start(source=SOURCE, url=url)
    logger.info(
        f"Collecting with mode={current_mode.value}",
        source=SOURCE,
        url=url,
        mode=current_mode.value,
    )

    status_code = None
    error_type = None

    try:
        # Collecter (pour l'instant, on n'a que DIRECT)
        # TODO: implémenter PROXY et BROWSER quand nécessaire
        item = fetch_footlocker_product(url)

        # Persister en base
        result = persist_deal(item)

        total_duration = (time.perf_counter() - start) * 1000

        # Enregistrer le succès
        record_outcome(
            source=SOURCE,
            mode=current_mode,
            success=True,
            status_code=200,
            duration_ms=total_duration,
        )

        logger.collect_success(
            source=SOURCE,
            url=url,
            duration_ms=total_duration,
        )
        logger.persist_success(
            source=SOURCE,
            external_id=item.external_id,
            action=result["action"],
        )

        return {
            "trace_id": trace_id,
            "mode": current_mode.value,
            "item": item.model_dump(),
            "persistence": result,
            "duration_ms": round(total_duration, 2),
        }

    except BlockedError as e:
        duration = (time.perf_counter() - start) * 1000
        status_code = e.status_code
        error_type = "BlockedError"

        # Enregistrer l'échec
        record_outcome(
            source=SOURCE,
            mode=current_mode,
            success=False,
            status_code=status_code,
            error_type=error_type,
            duration_ms=duration,
        )

        # Vérifier si on doit escalader
        new_mode = should_escalate(SOURCE, error_type)
        if new_mode:
            logger.warning(
                f"Escalating source mode",
                source=SOURCE,
                from_mode=current_mode.value,
                to_mode=new_mode.value,
            )

        logger.collect_error(
            source=SOURCE,
            url=url,
            error=e,
            duration_ms=duration,
        )
        raise

    except CollectorError as e:
        duration = (time.perf_counter() - start) * 1000
        status_code = getattr(e, "status_code", None)
        error_type = type(e).__name__

        # Enregistrer l'échec
        record_outcome(
            source=SOURCE,
            mode=current_mode,
            success=False,
            status_code=status_code,
            error_type=error_type,
            duration_ms=duration,
        )

        # Vérifier si on doit escalader
        new_mode = should_escalate(SOURCE, error_type)
        if new_mode:
            logger.warning(
                f"Escalating source mode",
                source=SOURCE,
                from_mode=current_mode.value,
                to_mode=new_mode.value,
            )

        logger.collect_error(
            source=SOURCE,
            url=url,
            error=e,
            duration_ms=duration,
        )
        raise

    except Exception as e:
        duration = (time.perf_counter() - start) * 1000
        error_type = type(e).__name__

        # Enregistrer l'échec
        record_outcome(
            source=SOURCE,
            mode=current_mode,
            success=False,
            error_type=error_type,
            duration_ms=duration,
        )

        logger.error(
            f"Unexpected error: {e}",
            source=SOURCE,
            url=url,
            duration_ms=duration,
            error_type=error_type,
        )
        raise
