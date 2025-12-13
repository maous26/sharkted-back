import time

from app.collectors.sources.size import fetch_size_product
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

SOURCE = "size"


def collect_size_product(url: str) -> dict:
    """
    Job RQ avec support policy hybride pour Size UK.
    """
    trace_id = set_trace_id()
    start = time.perf_counter()

    current_mode = get_current_mode(SOURCE)
    policy = get_policy(SOURCE)

    if current_mode == CollectMode.BLOCKED or not policy.enabled:
        logger.warning(
            "Source is blocked or disabled",
            source=SOURCE,
            url=url,
            mode=current_mode.value,
        )
        return {
            "trace_id": trace_id,
            "status": "skipped",
            "reason": f"Source {SOURCE} is {current_mode.value}",
        }

    logger.collect_start(source=SOURCE, url=url)

    try:
        item = fetch_size_product(url)
        result = persist_deal(item)

        total_duration = (time.perf_counter() - start) * 1000

        record_outcome(
            source=SOURCE,
            mode=current_mode,
            success=True,
            status_code=200,
            duration_ms=total_duration,
        )

        logger.collect_success(source=SOURCE, url=url, duration_ms=total_duration)
        logger.persist_success(source=SOURCE, external_id=item.external_id, action=result["action"])

        return {
            "trace_id": trace_id,
            "mode": current_mode.value,
            "item": item.model_dump(),
            "persistence": result,
            "duration_ms": round(total_duration, 2),
        }

    except BlockedError as e:
        duration = (time.perf_counter() - start) * 1000
        record_outcome(SOURCE, current_mode, False, e.status_code, "BlockedError", duration)
        should_escalate(SOURCE, "BlockedError")
        logger.collect_error(source=SOURCE, url=url, error=e, duration_ms=duration)
        raise

    except CollectorError as e:
        duration = (time.perf_counter() - start) * 1000
        record_outcome(SOURCE, current_mode, False, getattr(e, "status_code", None), type(e).__name__, duration)
        should_escalate(SOURCE, type(e).__name__)
        logger.collect_error(source=SOURCE, url=url, error=e, duration_ms=duration)
        raise

    except Exception as e:
        duration = (time.perf_counter() - start) * 1000
        record_outcome(SOURCE, current_mode, False, None, type(e).__name__, duration)
        logger.error(f"Unexpected error: {e}", source=SOURCE, url=url, duration_ms=duration)
        raise
