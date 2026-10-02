from __future__ import annotations

from datetime import UTC, datetime

import pytest
from shared.rule_engine import MatchContext, RawEvent, RawEventBatch
from streamer.core import bus


TS = datetime(2026, 3, 10, 16, 0, 0, tzinfo=UTC)


def _events(n: int) -> list[RawEvent]:
    return [RawEvent(employee="emp-1", ts=TS, context=MatchContext(click=f"c{i}")) for i in range(n)]


def test_encode_batch_roundtrips() -> None:
    events = _events(3)
    decoded = RawEventBatch.validate_json(bus.encode_batch(events))
    assert [e.context.click for e in decoded] == ["c0", "c1", "c2"]


async def test_publish_batch_sends_one_message() -> None:
    sent: list[tuple[str, bytes]] = []

    class FakeClient:
        async def publish(self, subject: str, data: bytes) -> None:
            sent.append((subject, data))

    events = _events(4)
    await bus.publish_batch(FakeClient(), "events.raw.emp-1", events)

    assert len(sent) == 1  # a whole batch is one NATS message, not four
    subject, data = sent[0]
    assert subject == "events.raw.emp-1"
    assert len(RawEventBatch.validate_json(data)) == 4


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
