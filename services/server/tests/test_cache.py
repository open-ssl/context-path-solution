from __future__ import annotations

import pytest
from server.core import cache


class FakeRedis:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_init_cache_builds_client_and_initializes_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_from_url(url: str) -> FakeRedis:
        captured["url"] = url
        return FakeRedis()

    def fake_init(backend: object, *, prefix: str) -> None:
        captured["backend"] = backend
        captured["prefix"] = prefix

    monkeypatch.setattr(cache.aioredis, "from_url", fake_from_url)
    monkeypatch.setattr(cache.FastAPICache, "init", fake_init)

    client = await cache.init_cache("redis://localhost:6379/0")

    assert isinstance(client, FakeRedis)
    assert captured["url"] == "redis://localhost:6379/0"
    assert captured["prefix"] == cache.CACHE_PREFIX


async def test_close_cache_closes_client() -> None:
    client = FakeRedis()
    await cache.close_cache(client)
    assert client.closed is True
