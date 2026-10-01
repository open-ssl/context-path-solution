from __future__ import annotations

from datetime import datetime, timezone

from shared.models.events import ClickEvent, ClipboardEvent, FocusEvent, OcrEvent
from shared.models.metadata import Metadata
from shared.models.popup import Popup
from shared.models.rule import RuleSet


def test_metadata_parses_displays() -> None:
    meta = Metadata.model_validate(
        {
            "schema_version": "1.0.0",
            "session_id": "20260310_160005",
            "employee_id": "emp-2",
            "started_at": "2026-03-10T16:00:05.751129600Z",
            "ended_at": "2026-03-10T17:00:06.484408Z",
            "machine": {
                "hostname": "DESK-1182",
                "os_version": "Windows 10.0 Build 26200",
                "displays": [{"id": 0, "bounds": [0, 0, 3840, 2160], "scale": 2.0, "primary": True}],
            },
        }
    )
    assert meta.employee_id == "emp-2"
    assert meta.machine.displays[0].bounds == (0, 0, 3840, 2160)


def test_rules_file_shape_parses() -> None:
    ruleset = RuleSet.model_validate(
        {
            "rules": [
                {
                    "id": "forwarded-email-opened",
                    "when": {"click": "FW:*", "process": ["OUTLOOK.EXE", "olk.exe"]},
                    "popup": {"title": "Forwarded email", "body": "You opened: {click}"},
                }
            ]
        }
    )
    rule = ruleset.rules[0]
    assert rule.when.click == "FW:*"
    assert rule.when.process == ["OUTLOOK.EXE", "olk.exe"]
    assert rule.when.ocr is None


def test_nanosecond_timestamp_is_accepted() -> None:
    event = ClickEvent.model_validate(
        {
            "ts": "2026-03-10T16:00:08.424459900Z",
            "event": "click",
            "button": "left",
            "mouse_x": 5875,
            "mouse_y": 278,
            "click_count": "single",
            "window_title": "Chrome Hiller Acetic",
            "process_name": "msedgewebview2.exe",
        }
    )
    assert event.mouse_x == 5875
    assert event.ts.tzinfo is not None


def test_ocr_event_tracks_deduplication() -> None:
    event = OcrEvent.model_validate(
        {
            "ts": "2026-03-10T16:00:06.698086900Z",
            "filename": "2026-03-10T16-00-05-316.jpg",
            "display_id": 1,
            "resolution": [3840, 2160],
            "ocr_text_blocks": [],
            "deduplicated_from": "2026-03-10T16-00-05-316.jpg",
        }
    )
    assert event.deduplicated_from == "2026-03-10T16-00-05-316.jpg"
    assert event.ocr_text_blocks == []


def test_clipboard_and_focus_events_parse() -> None:
    clip = ClipboardEvent.model_validate(
        {
            "ts": "2026-03-10T16:09:26.943781600Z",
            "event": "clipboard_change",
            "clipboard_content_text": "INV-99120",
            "clipboard_source_app": "chrome.exe",
        }
    )
    assert clip.clipboard_content_text == "INV-99120"

    focus = FocusEvent.model_validate(
        {
            "ts": "2026-03-10T16:02:24.336011Z",
            "event": "focus_change",
            "window_title": "Chat | Teams",
            "process_name": "ms-teams.exe",
            "window_rect": [2499, 2148, 2588, 1520],
        }
    )
    assert focus.process_name == "ms-teams.exe"
    assert focus.window_rect == (2499, 2148, 2588, 1520)


def test_popup_serializes_ts_to_iso() -> None:
    popup = Popup(
        employee="emp-1",
        rule="forwarded-email-opened",
        ts=datetime(2026, 3, 10, 16, 0, 8, tzinfo=timezone.utc),
        title="Forwarded email",
        body="You opened a forwarded email: FW: hi",
    )
    dumped = popup.model_dump(mode="json")
    assert dumped["ts"] == "2026-03-10T16:00:08Z"
    assert dumped["employee"] == "emp-1"
