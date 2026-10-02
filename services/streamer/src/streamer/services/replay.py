from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from shared.models.events import OcrEvent
from shared.models.metadata import Display
from shared.rule_engine import MatchContext, RawEvent, resolve_click_text

from streamer.services.archive import ArchiveReplay, SourceEvent


logger = logging.getLogger("streamer.services.replay")

# A batch sink: one coroutine call ships a whole interval's worth of RawEvents.
PublishBatchFn = Callable[[list[RawEvent]], Awaitable[None]]
# Given an employee id, returns the batch sink for that employee's isolated topic.
PublishFactory = Callable[[str], PublishBatchFn]
SleepFn = Callable[[float], Awaitable[None]]

DEFAULT_BATCH_SIZE = 200


@dataclass
class _EmployeeState:
    displays: list[Display]
    window_title: str | None = None
    process: str | None = None
    clipboard: str | None = None
    ocr: dict[int, OcrEvent] = field(default_factory=dict)


def _screen_text(state: _EmployeeState) -> str | None:
    blocks = [block.text for snapshot in state.ocr.values() for block in snapshot.ocr_text_blocks]
    return "\n".join(blocks) if blocks else None


def _advance(state: _EmployeeState, event: SourceEvent) -> MatchContext | None:
    """Apply an event to the running context. Focus/OCR only update state; clicks and
    clipboard changes are the actionable events that produce a MatchContext."""
    payload = event.payload
    if event.kind == "focus":
        state.window_title = payload.window_title or state.window_title
        state.process = payload.process_name or state.process
        return None
    if event.kind == "ocr":
        state.ocr[payload.display_id] = payload
        return None
    if event.kind == "clipboard":
        state.clipboard = payload.clipboard_content_text
        return MatchContext(
            process=payload.clipboard_source_app or state.process,
            window_title=state.window_title,
            clipboard=state.clipboard,
            ocr=_screen_text(state),
        )
    # click
    return MatchContext(
        click=resolve_click_text(payload.mouse_x, payload.mouse_y, state.displays, state.ocr),
        process=payload.process_name or state.process,
        window_title=payload.window_title or state.window_title,
        clipboard=state.clipboard,
        ocr=_screen_text(state),
    )


async def replay_archive(
    archive: ArchiveReplay,
    publish: PublishBatchFn,
    *,
    tick_ms: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep: SleepFn = asyncio.sleep,
) -> int:
    """Replay a single employee's archive to its isolated topic.

    The one-second tick is the unit of both pacing and batching:

    - **Delta-time fast-forward.** Between two populated seconds we sleep
      `delta_seconds * (tick_ms / 1000)` where `delta_seconds` is the gap between them,
      so an idle stretch (no events for N seconds) collapses to a single proportional
      pause instead of N per-second ticks, and a zero gap (same second) never pauses.
    - **Batching.** All actionable events in one tick are shipped as a single NATS
      message (also flushed when the buffer reaches `batch_size`), collapsing per-event
      round-trips.

    Returns the number of RawEvents published.
    """
    state = _EmployeeState(displays=archive.displays)
    buffer: list[RawEvent] = []
    published = 0
    prev_tick: int | None = None

    async def flush() -> None:
        nonlocal published
        if buffer:
            await publish(list(buffer))
            published += len(buffer)
            buffer.clear()

    for event in archive.events:
        tick = int(event.ts.timestamp())
        if prev_tick is not None and tick != prev_tick:
            # Finished a second: ship its batch, then fast-forward the idle gap.
            await flush()
            await sleep((tick - prev_tick) * (tick_ms / 1000.0))
        prev_tick = tick

        context = _advance(state, event)
        if context is not None:
            buffer.append(RawEvent(employee=archive.employee, ts=event.ts, context=context))
            if len(buffer) >= batch_size:
                await flush()

    await flush()
    logger.info("replay complete for %s: published %d RawEvent(s)", archive.employee, published)
    return published


async def run_workers(
    archives: list[ArchiveReplay],
    publish_factory: PublishFactory,
    *,
    tick_ms: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep: SleepFn = asyncio.sleep,
) -> int:
    """Run one independent worker per archive concurrently, each streaming to its own
    employee topic. Returns the total number of RawEvents published across all workers."""
    if not archives:
        return 0
    results = await asyncio.gather(
        *(
            replay_archive(
                archive, publish_factory(archive.employee), tick_ms=tick_ms, batch_size=batch_size, sleep=sleep
            )
            for archive in archives
        )
    )
    return sum(results)
