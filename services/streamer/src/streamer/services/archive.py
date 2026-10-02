from __future__ import annotations

import io
import json
import logging
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared.models.events import ClickEvent, ClipboardEvent, FocusEvent, OcrEvent
from shared.models.metadata import Display, Metadata


logger = logging.getLogger("streamer.services.archive")

SourcePayload = ClickEvent | ClipboardEvent | FocusEvent | OcrEvent

# Event files we care about for rule context, mapped to a kind tag. Other recorder
# streams (keyboard, scroll, drag) are irrelevant to the rule engine and skipped.
_EVENT_FILES = {
    "windows.jsonl": "focus",
    "mouse.jsonl": "click",
    "clipboard.jsonl": "clipboard",
    "ocr.jsonl": "ocr",
}


@dataclass(frozen=True)
class SourceEvent:
    ts: datetime
    employee: str
    kind: str
    payload: SourcePayload


@dataclass
class ArchiveReplay:
    employee: str
    displays: list[Display]
    events: list[SourceEvent]  # sorted chronologically by ts


def _iter_jsonl(blob: bytes):
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping malformed jsonl line")


def _resolve_ocr_dedup(raw_rows: list[dict]) -> list[OcrEvent]:
    """Fill empty OCR snapshots from the frame their `deduplicated_from` points at.

    The recorder omits text for frames identical to an earlier screenshot, leaving a
    `deduplicated_from` pointing at that earlier frame's filename. We reuse the blocks
    of the first frame seen for that filename.
    """
    canonical: dict[str, list] = {}
    for row in raw_rows:
        blocks = row.get("ocr_text_blocks") or []
        filename = row.get("filename")
        if blocks and filename and filename not in canonical:
            canonical[filename] = blocks

    resolved: list[OcrEvent] = []
    for row in raw_rows:
        if not (row.get("ocr_text_blocks") or []):
            source = row.get("deduplicated_from")
            if source and source in canonical:
                row = {**row, "ocr_text_blocks": canonical[source]}
        resolved.append(OcrEvent.model_validate(row))
    return resolved


def _parse_events(kind: str, blob: bytes, employee: str) -> list[SourceEvent]:
    if kind == "ocr":
        rows = list(_iter_jsonl(blob))
        return [SourceEvent(ts=ev.ts, employee=employee, kind=kind, payload=ev) for ev in _resolve_ocr_dedup(rows)]

    model = {"click": ClickEvent, "clipboard": ClipboardEvent, "focus": FocusEvent}[kind]
    expected_type = {"click": "click", "clipboard": "clipboard_change", "focus": "focus_change"}[kind]

    events: list[SourceEvent] = []
    for row in _iter_jsonl(blob):
        if row.get("event") != expected_type:
            continue
        payload = model.model_validate(row)
        events.append(SourceEvent(ts=payload.ts, employee=employee, kind=kind, payload=payload))
    return events


def read_archive(data: bytes) -> ArchiveReplay:
    """Parse a `*.tar.gz` recording from memory (never unpacked to disk)."""
    metadata: Metadata | None = None
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            handle = tar.extractfile(member)
            if handle is None:  # pragma: no cover - isfile() above guarantees a regular file
                continue
            payload = handle.read()
            if name == "metadata.json":
                metadata = Metadata.model_validate_json(payload)
            elif name in _EVENT_FILES:
                members[name] = payload

    if metadata is None:
        raise ValueError("archive is missing metadata.json")

    employee = metadata.employee_id
    events: list[SourceEvent] = []
    for name, kind in _EVENT_FILES.items():
        blob = members.get(name)
        if blob is not None:
            events.extend(_parse_events(kind, blob, employee))
    events.sort(key=lambda event: event.ts)

    logger.info("read archive for %s: %d event(s)", employee, len(events))
    return ArchiveReplay(employee=employee, displays=metadata.machine.displays, events=events)


def load_archives(data_dir: str) -> list[ArchiveReplay]:
    archives: list[ArchiveReplay] = []
    for path in sorted(Path(data_dir).glob("*.tar.gz")):
        archives.append(read_archive(path.read_bytes()))
    logger.info("loaded %d archive(s) from %s", len(archives), data_dir)
    return archives
