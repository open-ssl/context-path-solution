from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from shared.models.popup import Popup


logger = logging.getLogger("popup_writer.services.writer")

FlushFn = Callable[[list[Popup]], Awaitable[None]]


class BatchWriter:
    """Buffers popups and flushes them in bulk on a size threshold or a timer.

    The flush function is injected so the buffering logic can be unit-tested
    without a live database. Flushes are serialized by a lock; a failed flush
    re-queues its batch so no popup is lost (writes are idempotent downstream).
    """

    def __init__(self, flush: FlushFn, *, batch_size: int = 100, flush_interval: float = 5.0) -> None:
        self._flush = flush
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[Popup] = []
        self._lock = asyncio.Lock()
        self._stopped = asyncio.Event()
        self._timer: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._timer is None:
            self._stopped.clear()
            self._timer = asyncio.create_task(self._run_timer())

    async def add(self, popup: Popup) -> None:
        self._buffer.append(popup)
        if len(self._buffer) >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = self._buffer
            self._buffer = []
            try:
                await self._flush(batch)
            except Exception:
                # Re-queue the batch for a later attempt; downstream writes are idempotent.
                self._buffer[:0] = batch
                logger.exception("flush failed; re-queued %d popup(s)", len(batch))

    async def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            await self._timer
            self._timer = None
        await self.flush()

    async def _run_timer(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._flush_interval)
            except TimeoutError:
                await self.flush()
