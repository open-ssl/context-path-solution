from __future__ import annotations

from fastapi_cache.decorator import cache
from shared.models.popup import Popup

from server.db import repository


CACHE_TTL_SECONDS = 60


@cache(expire=CACHE_TTL_SECONDS)
async def _get_audits_cached(employee: str, rule: str | None) -> list[Popup]:
    return await repository.fetch_audits(employee=employee, rule=rule)


async def get_audits(employee: str | None = None, rule: str | None = None) -> list[Popup]:
    # Keyed queries go through the Redis-backed read-through cache; unkeyed queries hit
    # TimescaleDB directly to avoid caching heavy full scans.
    if employee:
        return await _get_audits_cached(employee=employee, rule=rule)
    return await repository.fetch_audits(employee=None, rule=rule)
