import time
import redis

def allow(redis_conn: redis.Redis, key: str, limit: int, window_sec: int) -> bool:
    """
    Sliding-ish window limiter: max `limit` events per `window_sec` for `key`.
    Clean, predictable, good enough for MVP.
    """
    now = int(time.time())
    bucket = f"rl:{key}:{now // window_sec}"
    p = redis_conn.pipeline()
    p.incr(bucket, 1)
    p.expire(bucket, window_sec + 2)
    count, _ = p.execute()
    return int(count) <= int(limit)
