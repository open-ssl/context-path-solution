from __future__ import annotations

import pytest
from agent.core import bus


async def test_connect_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    async def fake_connect(url: str) -> object:
        return sentinel

    monkeypatch.setattr(bus.nats, "connect", fake_connect)
    assert await bus.connect("nats://localhost:4222", retries=1, delay=0) is sentinel


async def test_connect_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}
    sentinel = object()

    async def flaky(url: str) -> object:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("not ready")
        return sentinel

    monkeypatch.setattr(bus.nats, "connect", flaky)
    assert await bus.connect("nats://localhost:4222", retries=3, delay=0) is sentinel
    assert attempts["n"] == 2


async def test_connect_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_fail(url: str) -> object:
        raise OSError("down")

    monkeypatch.setattr(bus.nats, "connect", always_fail)
    with pytest.raises(RuntimeError, match="could not connect to NATS"):
        await bus.connect("nats://localhost:4222", retries=2, delay=0)
