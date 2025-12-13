import os
import redis
from rq_scheduler import Scheduler
from datetime import timedelta
from app.jobs import collect_http_json

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

def main():
    r = redis.from_url(REDIS_URL)
    scheduler = Scheduler(connection=r, queue_name="default")

    # Exemple: toutes les 2 minutes (test)
    scheduler.schedule(
        scheduled_time=None,
        func=collect_http_json,
        args=["https://httpbin.org/json", "scheduled_test"],
        interval=120,
        repeat=None,
        result_ttl=3600,
    )

    # boucle interne du scheduler
    scheduler.run()

if __name__ == "__main__":
    main()
