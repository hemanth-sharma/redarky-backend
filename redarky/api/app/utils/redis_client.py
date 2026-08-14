# import redis
import redis.asyncio as redis

from app.config import settings

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

def is_duplicate(project_id: str, hash_key: str) -> bool:
    key = f"dedup:{project_id}"
    added = redis_client.sadd(key, hash_key)
    return added == 0
