from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OcrBlock(BaseModel):
    text: str
    bounding_box: tuple[int, int, int, int]
    confidence: float = 1.0


class OcrEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    filename: str
    display_id: int
    resolution: tuple[int, int] | None = None
    window_rect: tuple[int, int, int, int] | None = None
    ocr_text_blocks: list[OcrBlock] = []
    # When set, this snapshot reuses the text of the earlier snapshot with this filename.
    deduplicated_from: str | None = None


class ClickEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    event: str
    button: str | None = None
    mouse_x: int
    mouse_y: int
    click_count: str | None = None
    window_title: str | None = None
    process_name: str | None = None


class ClipboardEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    event: str
    clipboard_content_text: str | None = None
    clipboard_source_app: str | None = None


class FocusEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: datetime
    event: str
    window_title: str | None = None
    process_name: str | None = None
    window_rect: tuple[int, int, int, int] | None = None
