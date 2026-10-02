from __future__ import annotations

import heapq
import io
import json
import logging
import tarfile
from collections.abc import Callable, Iterator
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

# Mouse-move events dominate `mouse.jsonl` volume but never drive a rule. Reject them
# with a raw byte-substring check BEFORE `json.loads`/Pydantic so the hot path never
# pays to parse them. The recorder emits `"event": "move"` (space after the colon).
_MOVE_MARKER = b'"event": "move"'

_MODELS: dict[str, type[ClickEvent | ClipboardEvent | FocusEvent]] = {
    "click": ClickEvent,
    "clipboard": ClipboardEvent,
    "focus": FocusEvent,
}
_EXPECTED_TYPE = {"click": "click", "clipboard": "clipboard_change", "focus": "focus_change"}

# A factory that opens a fresh `TarFile` over the same source. Each JSONL member is read
# through its own handle so K files can be iterated concurrently for the K-way merge.
TarOpener = Callable[[], tarfile.TarFile]


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
    events: Iterator[SourceEvent]  # chronological by ts; lazy in production, a list in tests


def _scan(tar: tarfile.TarFile) -> tuple[Metadata, dict[str, str]]:
    """Read `metadata.json` and map each relevant event file's basename to its member
    name. Only `metadata.json` (small) is loaded; event bodies are streamed later."""
    metadata: Metadata | None = None
    members: dict[str, str] = {}
    for member in tar.getmembers():
        if not member.isfile():
            continue
        name = Path(member.name).name
        if name == "metadata.json":
            handle = tar.extractfile(member)
            if handle is not None:
                metadata = Metadata.model_validate_json(handle.read())
        elif name in _EVENT_FILES:
            members[name] = member.name
    if metadata is None:
        raise ValueError("archive is missing metadata.json")
    return metadata, members


def _iter_rows(open_tar: TarOpener, member_name: str, *, drop_moves: bool) -> Iterator[dict]:
    """Stream one JSONL member line by line. The tar handle is owned by this generator
    and closed when it is exhausted/discarded, so memory stays O(1) per member."""
    tar = open_tar()
    try:
        handle = tar.extractfile(member_name)
        if handle is None:  # pragma: no cover - members come from _scan's isfile() filter
            return
        for raw in handle:
            if drop_moves and _MOVE_MARKER in raw:
                continue
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("skipping malformed jsonl line in %s", member_name)
    finally:
        tar.close()


def _typed_stream(rows: Iterator[dict], kind: str, employee: str) -> Iterator[SourceEvent]:
    model = _MODELS[kind]
    expected = _EXPECTED_TYPE[kind]
    for row in rows:
        if row.get("event") != expected:
            continue
        payload = model.model_validate(row)
        yield SourceEvent(ts=payload.ts, employee=employee, kind=kind, payload=payload)


def _ocr_stream(rows: Iterator[dict], employee: str) -> Iterator[SourceEvent]:
    """Resolve `deduplicated_from` on the fly. The recorder omits text for frames
    identical to an earlier screenshot; since the canonical frame always precedes the
    frames that reference it, a running map of first-seen blocks resolves them in one
    pass without buffering the whole file."""
    canonical: dict[str, list] = {}
    for row in rows:
        blocks = row.get("ocr_text_blocks") or []
        filename = row.get("filename")
        if blocks:
            if filename and filename not in canonical:
                canonical[filename] = blocks
        else:
            source = row.get("deduplicated_from")
            if source and source in canonical:
                row = {**row, "ocr_text_blocks": canonical[source]}
        payload = OcrEvent.model_validate(row)
        yield SourceEvent(ts=payload.ts, employee=employee, kind="ocr", payload=payload)


def _merge_events(open_tar: TarOpener, members: dict[str, str], employee: str) -> Iterator[SourceEvent]:
    """K-way merge the per-file chronological streams into one chronological stream.

    `heapq.merge` pulls lazily from each sorted input, so the merge holds at most one
    row per open file (O(K)) rather than the whole archive.
    """
    streams: list[Iterator[SourceEvent]] = []
    for name, kind in _EVENT_FILES.items():
        member_name = members.get(name)
        if member_name is None:
            continue
        rows = _iter_rows(open_tar, member_name, drop_moves=(name == "mouse.jsonl"))
        streams.append(_ocr_stream(rows, employee) if kind == "ocr" else _typed_stream(rows, kind, employee))
    return heapq.merge(*streams, key=lambda event: event.ts)


def open_archive(path: Path) -> ArchiveReplay:
    """Open a `*.tar.gz` recording for streaming replay without unpacking it to disk or
    loading it whole into memory."""
    with tarfile.open(path, mode="r:gz") as tar:
        metadata, members = _scan(tar)
    events = _merge_events(lambda: tarfile.open(path, mode="r:gz"), members, metadata.employee_id)  # noqa: SIM115
    logger.info("opened archive for %s (%s)", metadata.employee_id, path.name)
    return ArchiveReplay(employee=metadata.employee_id, displays=metadata.machine.displays, events=events)


def read_archive(data: bytes) -> ArchiveReplay:
    """Parse an in-memory `*.tar.gz` and materialize its events (test/helper entry point).

    Uses the same streaming primitives as `open_archive`; here the merged stream is
    drained into a list so callers can index it. `io.BytesIO(data)` shares the immutable
    `data` buffer across handles, so no per-member copy is made.
    """
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        metadata, members = _scan(tar)
    open_tar: TarOpener = lambda: tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")  # noqa: E731, SIM115
    events = list(_merge_events(open_tar, members, metadata.employee_id))
    logger.info("read archive for %s: %d event(s)", metadata.employee_id, len(events))
    return ArchiveReplay(employee=metadata.employee_id, displays=metadata.machine.displays, events=events)


def discover_archives(data_dir: str) -> list[Path]:
    paths = sorted(Path(data_dir).glob("*.tar.gz"))
    logger.info("discovered %d archive(s) in %s", len(paths), data_dir)
    return paths


def load_archives(data_dir: str) -> list[ArchiveReplay]:
    """Open every archive in `data_dir` for lazy streaming (one `ArchiveReplay` each)."""
    return [open_archive(path) for path in discover_archives(data_dir)]
