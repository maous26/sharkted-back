import time
import logging

logger = logging.getLogger(__name__)

def test_job(message: str):
    logger.warning(f"[JOB START] {message}")
    time.sleep(2)
    logger.warning("[JOB END] job terminé avec succès")
    return {"status": "ok", "message": message}

from app.collectors.http_json import fetch_json

def collect_http_json(url: str, source: str):
    data, meta = fetch_json(url=url, source=source)
    return {"meta": meta, "data": data}

