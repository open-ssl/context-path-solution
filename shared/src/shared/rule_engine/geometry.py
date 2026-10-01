from __future__ import annotations

from shared.models.events import OcrEvent
from shared.models.metadata import Display


def display_for_point(x: int, y: int, displays: list[Display]) -> Display | None:
    for display in displays:
        bx, by, bw, bh = display.bounds
        if bx <= x < bx + bw and by <= y < by + bh:
            return display
    return None


def text_at_point(display: Display, snapshot: OcrEvent, x: int, y: int) -> str | None:
    bx, by, bw, bh = display.bounds
    res_w, res_h = snapshot.resolution if snapshot.resolution else (bw, bh)
    # OCR bounding boxes live in the snapshot image's pixel space, which may differ
    # from the display's virtual-desktop bounds; scale the point into that space.
    scale_x = res_w / bw if bw else 1.0
    scale_y = res_h / bh if bh else 1.0
    local_x = (x - bx) * scale_x
    local_y = (y - by) * scale_y
    for block in snapshot.ocr_text_blocks:
        ox, oy, ow, oh = block.bounding_box
        if ox <= local_x < ox + ow and oy <= local_y < oy + oh:
            return block.text
    return None


def resolve_click_text(
    x: int,
    y: int,
    displays: list[Display],
    snapshots: dict[int, OcrEvent],
) -> str | None:
    display = display_for_point(x, y, displays)
    if display is None:
        return None
    snapshot = snapshots.get(display.id)
    if snapshot is None:
        return None
    return text_at_point(display, snapshot, x, y)
