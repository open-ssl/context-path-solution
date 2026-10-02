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

PublishFn = Callable[[RawEvent], Awaitable[None]]
SleepFn = Callable[[float], Awaitable[None]]


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


def _merge(archives: list[ArchiveReplay]) -> tuple[list[SourceEvent], dict[str, _EmployeeState]]:
    states = {arc.employee: _EmployeeState(displays=arc.displays) for arc in archives}
    merged = [event for arc in archives for event in arc.events]
    merged.sort(key=lambda event: event.ts)
    return merged, states


async def replay(
    archives: list[ArchiveReplay],
    publish: PublishFn,
    *,
    tick_ms: int,
    sleep: SleepFn = asyncio.sleep,
) -> int:
    """Replay archives as a single chronological stream.

    Events are grouped into one-second ticks; between consecutive ticks we pause
    `tick_ms` to emulate the recording's pacing. Returns the number of RawEvents
    published. When every archive is exhausted the coroutine returns (graceful stop).
    """
    merged, states = _merge(archives)
    published = 0
    current_tick: int | None = None

    for event in merged:
        tick = int(event.ts.timestamp())
        if current_tick is not None and tick != current_tick:
            await sleep(tick_ms / 1000)
        current_tick = tick

        context = _advance(states[event.employee], event)
        if context is not None:
            await publish(RawEvent(employee=event.employee, ts=event.ts, context=context))
            published += 1

    logger.info("replay complete: published %d RawEvent(s)", published)
    return published
