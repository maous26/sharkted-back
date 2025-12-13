import os
from rq import Worker, Queue, Connection
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

def main():
    r = redis.from_url(REDIS_URL)
    with Connection(r):
        worker = Worker([Queue("high"), Queue("default"), Queue("low")])
        worker.work(with_scheduler=False)

if __name__ == "__main__":
    main()
