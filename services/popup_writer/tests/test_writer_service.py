from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from popup_writer.services.writer_service import BatchWriter
from shared.models.popup import Popup


def make_popup(n: int) -> Popup:
    return Popup(
        employee="emp-1",
        rule=f"rule-{n}",
        ts=datetime(2026, 3, 10, 16, 0, n, tzinfo=UTC),
        title="t",
        body="b",
    )


class Collector:
    def __init__(self) -> None:
        self.batches: list[list[Popup]] = []

    async def __call__(self, batch: list[Popup]) -> None:
        self.batches.append(list(batch))


async def test_flushes_on_batch_size() -> None:
    collector = Collector()
    writer = BatchWriter(collector, batch_size=3, flush_interval=100.0)

    await writer.add(make_popup(1))
    await writer.add(make_popup(2))
    assert collector.batches == []  # below threshold, nothing flushed yet

    await writer.add(make_popup(3))
    assert len(collector.batches) == 1
    assert len(collector.batches[0]) == 3


async def test_flushes_on_timer() -> None:
    collector = Collector()
    writer = BatchWriter(collector, batch_size=100, flush_interval=0.05)
    writer.start()
    try:
        await writer.add(make_popup(1))
        await asyncio.sleep(0.15)  # let at least one timer tick fire
        assert len(collector.batches) == 1
        assert len(collector.batches[0]) == 1
    finally:
        await writer.stop()


async def test_stop_flushes_remaining_buffer() -> None:
    collector = Collector()
    writer = BatchWriter(collector, batch_size=100, flush_interval=100.0)
    writer.start()

    await writer.add(make_popup(1))
    await writer.add(make_popup(2))
    assert collector.batches == []  # neither size nor timer triggered

    await writer.stop()  # graceful shutdown must drain the buffer
    assert len(collector.batches) == 1
    assert len(collector.batches[0]) == 2


async def test_empty_flush_is_noop() -> None:
    collector = Collector()
    writer = BatchWriter(collector, batch_size=10, flush_interval=100.0)
    await writer.flush()
    assert collector.batches == []


async def test_failed_flush_requeues_batch() -> None:
    attempts: list[int] = []

    async def flaky(batch: list[Popup]) -> None:
        attempts.append(len(batch))
        if len(attempts) == 1:
            raise RuntimeError("db down")

    writer = BatchWriter(flaky, batch_size=2, flush_interval=100.0)

    await writer.add(make_popup(1))
    await writer.add(make_popup(2))  # size reached -> first flush raises, batch re-queued
    assert attempts == [2]

    await writer.flush()  # retry succeeds, buffer drains
    assert attempts == [2, 2]

    await writer.flush()  # nothing left to flush
    assert attempts == [2, 2]
