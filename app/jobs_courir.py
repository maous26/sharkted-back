import time

from app.collectors.sources.courir import fetch_courir_product
from app.services.deal_service import persist_deal
from app.core.logging import get_logger, set_trace_id
from app.core.exceptions import CollectorError

logger = get_logger(__name__)


def collect_courir_product(url: str) -> dict:
    """
    Job RQ:
    - appelle le collector Courir
    - persiste le deal en base
    - retourne les infos de persistance
    """
    trace_id = set_trace_id()
    start = time.perf_counter()

    logger.collect_start(source="courir", url=url)

    try:
        # Collecter
        item = fetch_courir_product(url)

        # Persister en base
        result = persist_deal(item)

        total_duration = (time.perf_counter() - start) * 1000
        logger.collect_success(
            source="courir",
            url=url,
            duration_ms=total_duration,
        )
        logger.persist_success(
            source="courir",
            external_id=item.external_id,
            action=result["action"],
        )

        return {
            "trace_id": trace_id,
            "item": item.model_dump(),
            "persistence": result,
            "duration_ms": round(total_duration, 2),
        }

    except CollectorError as e:
        duration = (time.perf_counter() - start) * 1000
        logger.collect_error(
            source="courir",
            url=url,
            error=e,
            duration_ms=duration,
        )
        raise

    except Exception as e:
        duration = (time.perf_counter() - start) * 1000
        logger.error(
            f"Unexpected error: {e}",
            source="courir",
            url=url,
            duration_ms=duration,
            error_type=type(e).__name__,
        )
        raise
