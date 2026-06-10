import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

# NOTE — schema change 2026-04-30 (feat/inventory-photo-classification):
# The inventory cache (key: tools.inventory_tools.INVENTORY_CACHE_KEY,
# i.e. "inventory_dataset") now stores listings with the URL-based
# floorplan classifier already applied (image_url = first exterior or "",
# floorplan_url = first floorplan, real_photos = exteriors then
# floorplans). Pre-existing cached entries from before this deploy still
# have the old shape (image_url = floorplan URL). After deploy, run a
# one-time cache flush so clients refetch the corrected shape:
#   redis-cli DEL inventory_dataset
# (or restart the service to drop the local fallback cache).

# Try to import redis, but don't fail if not installed (for local dev without it)
try:
    import redis
except ImportError:
    redis = None

# Configure logging
logger = logging.getLogger(__name__)

# Global instances
_redis_client = None
_local_cache = {}


def get_redis_client():
    """
    Get or create a Redis client.
    Returns None if REDIS_HOST is not set or redis module is missing.
    """
    global _redis_client

    if _redis_client:
        return _redis_client

    redis_host = os.environ.get("REDIS_HOST")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))

    if not redis or not redis_host:
        return None

    try:
        _redis_client = redis.Redis(
            host=redis_host, port=redis_port, db=0, decode_responses=True, socket_connect_timeout=2
        )
        # Test connection
        _redis_client.ping()
        logger.info(f"Connected to Redis at {redis_host}:{redis_port}")
        return _redis_client
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {e}")
        _redis_client = None
        return None


def cache_get(key: str) -> Any | None:
    """
    Retrieve value from cache (Redis -> Local).
    Returns None if key not found or expired.
    """
    # 1. Try Redis
    client = get_redis_client()
    if client:
        try:
            value = client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            logger.warning(f"Redis get error for {key}: {e}")

    # 2. Try Local Memory (Fallback)
    if key in _local_cache:
        item = _local_cache[key]
        if item["expires_at"] > datetime.now():
            return item["value"]
        else:
            del _local_cache[key]  # Cleanup expired

    return None


def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> bool:
    """
    Set value in cache (Redis -> Local).
    TTL defaults to 5 minutes.
    """
    # 1. Try Redis
    client = get_redis_client()
    if client:
        try:
            serialized = json.dumps(value)
            client.setex(key, ttl_seconds, serialized)
        except Exception as e:
            logger.warning(f"Redis set error for {key}: {e}")

    # 2. Always update local cache as backup (or primary if Redis failed/missing)
    _local_cache[key] = {
        "value": value,
        "expires_at": datetime.now() + timedelta(seconds=ttl_seconds),
    }

    return True


def cache_delete(key: str):
    """Delete a key from all caches."""
    client = get_redis_client()
    if client:
        try:
            client.delete(key)
        except Exception:
            pass

    if key in _local_cache:
        del _local_cache[key]


def clear_local_cache():
    """Clear the local in-memory cache."""
    global _local_cache
    _local_cache = {}
