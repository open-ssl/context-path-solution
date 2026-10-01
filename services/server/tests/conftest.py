from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from httpx import ASGITransport
from server.main import app


@pytest.fixture(scope="session", autouse=True)
def _cache_backend() -> None:
    # Hermetic in-memory cache so tests exercise the read-through path without Redis.
    FastAPICache.init(InMemoryBackend(), prefix="test-cache")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport does not run lifespan, so no real asyncpg/Redis pools are opened.
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
