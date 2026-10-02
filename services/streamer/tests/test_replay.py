from __future__ import annotations

import random
from datetime import UTC, datetime

from shared.models.events import ClickEvent, ClipboardEvent, FocusEvent, OcrBlock, OcrEvent
from shared.models.metadata import Display
from shared.models.rule import RuleSet
from shared.rule_engine import RawEvent, RuleEngine
from streamer.services.archive import ArchiveReplay, SourceEvent
from streamer.services.replay import replay_archive, run_workers


DISPLAY = Display(id=0, bounds=(0, 0, 1920, 1080), scale=1.0, primary=True)
T0 = datetime(2026, 3, 10, 16, 0, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return datetime.fromtimestamp(T0.timestamp() + seconds, tz=UTC)


def _focus(emp: str, ts: datetime, *, window: str, process: str) -> SourceEvent:
    return SourceEvent(
        ts=ts,
        employee=emp,
        kind="focus",
        payload=FocusEvent(ts=ts, event="focus_change", window_title=window, process_name=process),
    )


def _ocr(emp: str, ts: datetime, *, text: str, box: tuple[int, int, int, int]) -> SourceEvent:
    payload = OcrEvent(
        ts=ts,
        filename="f.jpg",
        display_id=0,
        resolution=(1920, 1080),
        ocr_text_blocks=[OcrBlock(text=text, bounding_box=box)],
    )
    return SourceEvent(ts=ts, employee=emp, kind="ocr", payload=payload)


def _click(emp: str, ts: datetime, *, x: int, y: int) -> SourceEvent:
    return SourceEvent(
        ts=ts, employee=emp, kind="click", payload=ClickEvent(ts=ts, event="click", button="left", mouse_x=x, mouse_y=y)
    )


def _clipboard(emp: str, ts: datetime, *, text: str, app: str) -> SourceEvent:
    payload = ClipboardEvent(ts=ts, event="clipboard_change", clipboard_content_text=text, clipboard_source_app=app)
    return SourceEvent(ts=ts, employee=emp, kind="clipboard", payload=payload)


def _ocr_multi(emp: str, ts: datetime, *, blocks: list[tuple[str, tuple[int, int, int, int]]]) -> SourceEvent:
    payload = OcrEvent(
        ts=ts,
        filename="f.jpg",
        display_id=0,
        resolution=(1920, 1080),
        ocr_text_blocks=[OcrBlock(text=text, bounding_box=box) for text, box in blocks],
    )
    return SourceEvent(ts=ts, employee=emp, kind="ocr", payload=payload)


class Collector:
    """Collects published batches; `published` is the flattened event stream."""

    def __init__(self) -> None:
        self.batches: list[list[RawEvent]] = []

    async def __call__(self, events: list[RawEvent]) -> None:
        self.batches.append(events)

    @property
    def published(self) -> list[RawEvent]:
        return [event for batch in self.batches for event in batch]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _first_context(events: list[SourceEvent]) -> RawEvent | None:
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()
    await replay_archive(archive, collector, tick_ms=0, sleep=FakeSleep())
    return collector.published[0] if collector.published else None


async def test_click_context_inherits_running_state_and_resolves_ocr() -> None:
    events = [
        _focus("emp-1", _at(0.0), window="Acme | Salesforce", process="chrome.exe"),
        _ocr("emp-1", _at(0.1), text="Delete", box=(100, 100, 50, 20)),
        _click("emp-1", _at(0.2), x=120, y=110),
    ]
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()

    published = await replay_archive(archive, collector, tick_ms=25, sleep=FakeSleep())

    assert published == 1
    ctx = collector.published[0].context
    assert ctx.click == "Delete"  # resolved via OCR bounding box under the click
    assert ctx.process == "chrome.exe"
    assert ctx.window_title == "Acme | Salesforce"
    assert ctx.ocr == "Delete"


async def test_clipboard_event_builds_context() -> None:
    events = [_clipboard("emp-1", _at(0.0), text="INV-99120", app="OUTLOOK.EXE")]
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()

    published = await replay_archive(archive, collector, tick_ms=0, sleep=FakeSleep())

    assert published == 1
    ctx = collector.published[0].context
    assert ctx.clipboard == "INV-99120"
    assert ctx.process == "OUTLOOK.EXE"


async def test_delta_time_pacing_skips_idle_gaps() -> None:
    events = [
        _click("emp-1", _at(0.0), x=1, y=1),
        _click("emp-1", _at(0.2), x=1, y=1),  # same second -> no pause
        _click("emp-1", _at(1.0), x=1, y=1),  # next second -> one pause
        _click("emp-1", _at(5.0), x=1, y=1),  # +4 idle seconds -> one fast-forward pause
    ]
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()
    sleep = FakeSleep()

    # tick_ms=1000 -> factor 1.0, so each sleep equals the gap in whole seconds.
    published = await replay_archive(archive, collector, tick_ms=1000, sleep=sleep)

    assert published == 4
    # Same-second events never pause; the 4-second idle gap is one sleep, not four ticks.
    assert sleep.calls == [1.0, 4.0]
    # Batched per second: {sec 0: 2 events}, {sec 1: 1}, {sec 5: 1}.
    assert [len(batch) for batch in collector.batches] == [2, 1, 1]


async def test_events_in_same_interval_ship_as_one_batch() -> None:
    events = [
        _click("emp-1", _at(0.0), x=1, y=1),
        _click("emp-1", _at(0.0), x=2, y=2),
        _click("emp-1", _at(0.0), x=3, y=3),
        _click("emp-1", _at(1.0), x=4, y=4),
        _click("emp-1", _at(1.0), x=5, y=5),
    ]
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()
    sleep = FakeSleep()

    published = await replay_archive(archive, collector, tick_ms=25, sleep=sleep)

    assert published == 5
    # One batch per interval: the three @0.0 events flush before the single 1.0s pause.
    assert [len(batch) for batch in collector.batches] == [3, 2]
    assert sleep.calls == [0.025]


async def test_batch_size_cap_splits_a_burst() -> None:
    events = [_click("emp-1", _at(0.0), x=i, y=i) for i in range(5)]  # all same instant
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=events)
    collector = Collector()
    sleep = FakeSleep()

    published = await replay_archive(archive, collector, tick_ms=25, batch_size=2, sleep=sleep)

    assert published == 5
    assert [len(batch) for batch in collector.batches] == [2, 2, 1]
    assert sleep.calls == []  # no delta between same-instant events


async def test_empty_archive_publishes_nothing() -> None:
    archive = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=[])
    collector = Collector()
    published = await replay_archive(archive, collector, tick_ms=25, sleep=FakeSleep())
    assert published == 0
    assert collector.batches == []


async def test_run_workers_streams_each_employee_to_its_own_sink() -> None:
    archive_a = ArchiveReplay(employee="emp-1", displays=[DISPLAY], events=[_click("emp-1", _at(0.0), x=1, y=1)])
    archive_b = ArchiveReplay(employee="emp-2", displays=[DISPLAY], events=[_click("emp-2", _at(0.5), x=1, y=1)])
    sinks: dict[str, Collector] = {}

    def factory(employee: str) -> Collector:
        collector = sinks.setdefault(employee, Collector())
        return collector

    total = await run_workers([archive_a, archive_b], factory, tick_ms=0, sleep=FakeSleep())

    assert total == 2
    assert set(sinks) == {"emp-1", "emp-2"}
    assert [e.employee for e in sinks["emp-1"].published] == ["emp-1"]
    assert [e.employee for e in sinks["emp-2"].published] == ["emp-2"]


async def test_run_workers_empty_is_noop() -> None:
    assert await run_workers([], lambda employee: Collector(), tick_ms=25, sleep=FakeSleep()) == 0


async def test_randomized_click_coordinates_hit_and_miss_bounding_box() -> None:
    box = (100, 100, 50, 20)  # x, y, w, h  -> covers x in [100,150), y in [100,120)
    rng = random.Random(2024)
    for _ in range(60):
        inside = rng.random() < 0.5
        if inside:
            x = rng.randint(100, 149)
            y = rng.randint(100, 119)
        else:
            x = rng.randint(0, 1919)
            y = rng.randint(0, 1079)
            while 100 <= x < 150 and 100 <= y < 120:  # force outside the box
                x = rng.randint(0, 1919)
                y = rng.randint(0, 1079)
        events = [_ocr_multi("emp-1", _at(0.0), blocks=[("Delete", box)]), _click("emp-1", _at(0.1), x=x, y=y)]
        published = await _first_context(events)
        assert published is not None
        assert published.context.click == ("Delete" if inside else None)


async def test_click_outside_all_displays_resolves_none() -> None:
    events = [
        _ocr_multi("emp-1", _at(0.0), blocks=[("Delete", (100, 100, 50, 20))]),
        _click("emp-1", _at(0.1), x=100_000, y=100_000),  # beyond any display bounds
    ]
    published = await _first_context(events)
    assert published is not None
    assert published.context.click is None


async def test_ocr_noise_triggers_rule_end_to_end() -> None:
    rule = {
        "rules": [
            {
                "id": "salesforce-record-deleted",
                "when": {
                    "click": "Delete",
                    "window_title": "*| Salesforce*",
                    "ocr": "Are you sure you want to delete*",
                },
                "popup": {"title": "Record deleted", "body": "Deleted in {window_title}"},
            }
        ]
    }
    events = [
        _focus("emp-1", _at(0.0), window="Acme Lead | Salesforce", process="chrome.exe"),
        _ocr_multi(
            "emp-1",
            _at(0.1),
            blocks=[
                ("Home   Help   Settings", (0, 0, 300, 10)),
                ("Are you sure you want to delete this account?", (300, 300, 500, 20)),
                ("Delete", (100, 100, 50, 20)),
            ],
        ),
        _click("emp-1", _at(0.2), x=120, y=110),  # lands on the Delete button block
    ]
    published = await _first_context(events)
    assert published is not None

    engine = RuleEngine(RuleSet.model_validate(rule).rules)
    matches = engine.evaluate(published.context)
    assert [m.rule for m in matches] == ["salesforce-record-deleted"]
    assert matches[0].body == "Deleted in Acme Lead | Salesforce"
