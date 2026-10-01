from __future__ import annotations

from shared.models.events import OcrBlock, OcrEvent
from shared.models.metadata import Display
from shared.rule_engine.geometry import (
    display_for_point,
    resolve_click_text,
    text_at_point,
)


def _snapshot(display_id: int, blocks: list[OcrBlock], resolution=None) -> OcrEvent:
    return OcrEvent(
        ts="2026-03-10T16:00:05.316823800Z",
        filename="snap.jpg",
        display_id=display_id,
        resolution=resolution,
        ocr_text_blocks=blocks,
    )


def test_display_for_point_selects_correct_monitor() -> None:
    displays = [
        Display(id=0, bounds=(0, 0, 3840, 2160)),
        Display(id=1, bounds=(3852, 2, 3840, 2160)),
    ]
    assert display_for_point(100, 100, displays).id == 0
    assert display_for_point(5000, 500, displays).id == 1
    assert display_for_point(-5, 0, displays) is None


def test_text_at_point_hits_block() -> None:
    display = Display(id=0, bounds=(0, 0, 3840, 2160))
    snapshot = _snapshot(0, [OcrBlock(text="Forward", bounding_box=(810, 301, 83, 18))])
    assert text_at_point(display, snapshot, 820, 310) == "Forward"
    assert text_at_point(display, snapshot, 10, 10) is None


def test_text_at_point_scales_into_image_space() -> None:
    # Display bounds are half the OCR image resolution, so points must be scaled x2.
    display = Display(id=2, bounds=(0, 0, 1280, 800))
    snapshot = _snapshot(2, [OcrBlock(text="Delete", bounding_box=(200, 100, 40, 20))], resolution=(2560, 1600))
    assert text_at_point(display, snapshot, 105, 55) == "Delete"


def test_resolve_click_text_end_to_end() -> None:
    displays = [
        Display(id=0, bounds=(0, 0, 3840, 2160)),
        Display(id=1, bounds=(3852, 2, 3840, 2160)),
    ]
    snapshots = {
        1: _snapshot(1, [OcrBlock(text="FW: Quarterly report", bounding_box=(100, 100, 400, 30))]),
    }
    # Global point lands on display 1 at local (100 + offset) => inside the block.
    assert resolve_click_text(3952, 110, displays, snapshots) == "FW: Quarterly report"
    # No snapshot for display 0.
    assert resolve_click_text(100, 100, displays, snapshots) is None
