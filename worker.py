#!/usr/bin/env python3
"""
Worker RQ avec logging JSON structuré.
Usage: python worker.py <queue_name>
"""
import sys
import os
from rq import Worker, Queue, Connection
import redis

# Setup structured logging avant tout
from app.core.logging import setup_logging
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))

# Redis connection
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_conn = redis.from_url(REDIS_URL)


def main():
    queue_names = sys.argv[1:] if len(sys.argv) > 1 else ["default"]

    with Connection(redis_conn):
        queues = [Queue(name) for name in queue_names]
        worker = Worker(queues)
        worker.work()


if __name__ == "__main__":
    main()
