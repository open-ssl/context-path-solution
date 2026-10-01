from __future__ import annotations

import logging

from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis import asyncio as aioredis


logger = logging.getLogger("server.core.cache")

CACHE_PREFIX = "fastapi-cache"


async def init_cache(redis_url: str) -> aioredis.Redis:
    client: aioredis.Redis = aioredis.from_url(redis_url)
    FastAPICache.init(RedisBackend(client), prefix=CACHE_PREFIX)
    logger.info("cache backend ready")
    return client


async def close_cache(client: aioredis.Redis) -> None:
    await client.aclose()
    logger.info("cache client closed")
