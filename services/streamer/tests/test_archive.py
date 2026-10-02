from __future__ import annotations

import io
import json
import random
import tarfile
from pathlib import Path

import pytest
from streamer.services.archive import load_archives, read_archive


METADATA = {
    "schema_version": "1.0.0",
    "session_id": "20260310_160005",
    "employee_id": "emp-1",
    "started_at": "2026-03-10T16:00:00Z",
    "ended_at": "2026-03-10T17:00:00Z",
    "machine": {
        "hostname": "DESK-1",
        "os_version": "Windows 10",
        "displays": [{"id": 0, "bounds": [0, 0, 1920, 1080], "scale": 1.0, "primary": True}],
    },
}

OCR_ROWS = [
    {
        "ts": "2026-03-10T16:00:01Z",
        "filename": "a.jpg",
        "display_id": 0,
        "resolution": [1920, 1080],
        "ocr_text_blocks": [{"text": "Hello", "bounding_box": [0, 0, 10, 10], "confidence": 1.0}],
    },
    {
        "ts": "2026-03-10T16:00:02Z",
        "filename": "b.jpg",
        "display_id": 0,
        "resolution": [1920, 1080],
        "ocr_text_blocks": [],
        "deduplicated_from": "a.jpg",
    },
]

MOUSE_ROWS = [
    {"ts": "2026-03-10T16:00:03Z", "event": "move", "mouse_x": 1, "mouse_y": 1},  # filtered out
    {
        "ts": "2026-03-10T16:00:03.5Z",
        "event": "click",
        "button": "left",
        "mouse_x": 5,
        "mouse_y": 5,
        "window_title": "W",
        "process_name": "chrome.exe",
    },
]

CLIPBOARD_ROWS = [
    {
        "ts": "2026-03-10T16:00:04Z",
        "event": "clipboard_change",
        "clipboard_content_text": "INV-1",
        "clipboard_source_app": "OUTLOOK.EXE",
    }
]

WINDOWS_ROWS = [
    {
        "ts": "2026-03-10T16:00:00Z",
        "event": "focus_change",
        "window_title": "W1",
        "process_name": "p1",
        "window_rect": [0, 0, 10, 10],
    }
]


def _jsonl(rows: list[dict]) -> bytes:
    return "\n".join(json.dumps(row) for row in rows).encode("utf-8")


def build_targz(files: dict[str, bytes], *, dirs: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for dirname in dirs:
            info = tarfile.TarInfo(name=dirname)
            info.type = tarfile.DIRTYPE  # real directory member -> not a file
            tar.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def build_archive(
    *,
    ocr: list[dict] | None = None,
    mouse: list[dict] | None = None,
    clipboard: list[dict] | None = None,
    windows: list[dict] | None = None,
    metadata: dict | None = None,
    include_metadata: bool = True,
    raw_overrides: dict[str, bytes] | None = None,
    dirs: tuple[str, ...] = (),
) -> bytes:
    base = "20260310_160005"
    files: dict[str, bytes] = {}
    if include_metadata:
        files[f"{base}/metadata.json"] = json.dumps(metadata or METADATA).encode("utf-8")
    files[f"{base}/events/ocr.jsonl"] = _jsonl(ocr if ocr is not None else OCR_ROWS)
    files[f"{base}/events/mouse.jsonl"] = _jsonl(mouse if mouse is not None else MOUSE_ROWS)
    files[f"{base}/events/clipboard.jsonl"] = _jsonl(clipboard if clipboard is not None else CLIPBOARD_ROWS)
    files[f"{base}/events/windows.jsonl"] = _jsonl(windows if windows is not None else WINDOWS_ROWS)
    for name, blob in (raw_overrides or {}).items():
        files[f"{base}/events/{name}"] = blob
    return build_targz(files, dirs=dirs)


def test_read_archive_extracts_metadata_and_displays() -> None:
    replay = read_archive(build_archive())

    assert replay.employee == "emp-1"
    assert len(replay.displays) == 1
    assert replay.displays[0].bounds == (0, 0, 1920, 1080)


def test_events_parsed_sorted_and_click_filtered() -> None:
    replay = read_archive(build_archive())

    kinds = [event.kind for event in replay.events]
    # Chronological: focus(00), ocr(01), ocr(02), click(03.5), clipboard(04).
    assert kinds == ["focus", "ocr", "ocr", "click", "clipboard"]
    # The non-click "move" row is dropped.
    assert sum(1 for event in replay.events if event.kind == "click") == 1


def test_deduplicated_ocr_reuses_referenced_blocks() -> None:
    replay = read_archive(build_archive())

    ocr_events = [event.payload for event in replay.events if event.kind == "ocr"]
    deduped = next(event for event in ocr_events if event.filename == "b.jpg")
    assert [block.text for block in deduped.ocr_text_blocks] == ["Hello"]


def test_missing_metadata_raises() -> None:
    with pytest.raises(ValueError, match="metadata.json"):
        read_archive(build_archive(include_metadata=False))


def test_directory_members_are_skipped() -> None:
    # A directory entry in the tar must not break parsing.
    replay = read_archive(build_archive(dirs=("20260310_160005/events",)))
    assert replay.employee == "emp-1"


def test_broken_and_empty_jsonl_lines_are_skipped() -> None:
    good = json.dumps(MOUSE_ROWS[1]).encode("utf-8")
    blob = b"\n".join([b"", b"{not valid json", good, b"   ", b"{}"])
    replay = read_archive(build_archive(raw_overrides={"mouse.jsonl": blob}))
    # Only the one valid click survives; broken/empty/irrelevant lines are dropped.
    assert sum(1 for event in replay.events if event.kind == "click") == 1


def test_missing_event_file_is_tolerated() -> None:
    replay = read_archive(build_archive(raw_overrides={"clipboard.jsonl": b""}))
    assert all(event.kind != "clipboard" for event in replay.events)


def test_duplicate_canonical_filename_keeps_first_blocks() -> None:
    ocr = [
        {
            "ts": "2026-03-10T16:00:01Z",
            "filename": "a.jpg",
            "display_id": 0,
            "ocr_text_blocks": [{"text": "FIRST", "bounding_box": [0, 0, 5, 5]}],
        },
        {
            "ts": "2026-03-10T16:00:02Z",
            "filename": "a.jpg",
            "display_id": 0,
            "ocr_text_blocks": [{"text": "SECOND", "bounding_box": [0, 0, 5, 5]}],
        },
        {
            "ts": "2026-03-10T16:00:03Z",
            "filename": "c.jpg",
            "display_id": 0,
            "ocr_text_blocks": [],
            "deduplicated_from": "a.jpg",
        },
    ]
    replay = read_archive(build_archive(ocr=ocr))
    deduped = next(e.payload for e in replay.events if e.kind == "ocr" and e.payload.filename == "c.jpg")
    assert [block.text for block in deduped.ocr_text_blocks] == ["FIRST"]


def test_load_archives_reads_files_from_disk(tmp_path: Path) -> None:
    (tmp_path / "emp-1.tar.gz").write_bytes(build_archive())
    (tmp_path / "emp-2.tar.gz").write_bytes(build_archive(metadata={**METADATA, "employee_id": "emp-2"}))
    (tmp_path / "ignore.txt").write_text("not an archive")

    archives = load_archives(str(tmp_path))

    assert [archive.employee for archive in archives] == ["emp-1", "emp-2"]


def test_load_archives_empty_dir(tmp_path: Path) -> None:
    assert load_archives(str(tmp_path)) == []


def test_randomized_ocr_dedup_chain_resolves() -> None:
    rng = random.Random(7)
    ocr: list[dict] = []
    # One canonical frame with text, then many dedup frames pointing back to it.
    ocr.append(
        {
            "ts": "2026-03-10T16:00:00Z",
            "filename": "canon.jpg",
            "display_id": 0,
            "ocr_text_blocks": [{"text": "Invoice total", "bounding_box": [0, 0, 10, 10]}],
        }
    )
    dedup_count = rng.randint(5, 20)
    for i in range(dedup_count):
        ocr.append(
            {
                "ts": f"2026-03-10T16:00:{i + 1:02d}Z",
                "filename": f"d{i}.jpg",
                "display_id": 0,
                "ocr_text_blocks": [],
                "deduplicated_from": "canon.jpg",
            }
        )

    replay = read_archive(build_archive(ocr=ocr))
    resolved = [e.payload for e in replay.events if e.kind == "ocr" and e.payload.filename.startswith("d")]
    assert len(resolved) == dedup_count
    assert all([block.text for block in snap.ocr_text_blocks] == ["Invoice total"] for snap in resolved)
