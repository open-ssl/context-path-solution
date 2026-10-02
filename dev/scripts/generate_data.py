#!/usr/bin/env python3
"""Synthetic dataset generator for ContextPath.

Produces streamer-compatible ``emp-{i}.tar.gz`` archives in ``OUTPUT_DIR``. Each
archive mirrors the recorder layout the ``streamer`` service expects:

    /metadata.json
    /events/windows.jsonl    (focus_change events)
    /events/mouse.jsonl      (move / click events)
    /events/clipboard.jsonl  (clipboard_change events)
    /events/ocr.jsonl        (OCR snapshots with bounding boxes)

Most mouse events are ``"event": "move"`` noise (dropped by the streamer's byte
pre-filter); a handful of click / clipboard / OCR sequences are crafted so every
rule in ``rules.json`` fires at least once, giving ``make run`` real popups out of
the box. Standard library only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("generate_data")

# Single virtual display; OCR resolution equals the display bounds (scale 1.0), so a
# click at a block's center resolves to that block's text (see rule_engine/geometry.py).
SCREEN_W = 1920
SCREEN_H = 1080

OUTLOOK_APPS = ("OUTLOOK.EXE", "olk.exe")
NOISE_WINDOWS = ("Inbox - Outlook", "Slack", "Chrome - Dashboard", "Notepad", "Explorer", "Teams")
NOISE_PROCESSES = ("chrome.exe", "slack.exe", "explorer.exe", "notepad.exe", "Teams.exe", "code.exe")
NOISE_CLIPBOARD = ("hello world", "TODO: refactor", "https://example.com", "see attached", "lgtm")
LOREM = ("Dashboard", "Overview", "Settings", "Reports", "Account", "Help", "Search results", "Recent files")

# How many actionable (non-move) events between randomly-injected rule triggers.
TRIGGER_INTERVAL = (12, 36)
# Upper bound on rows a single scenario can add; used to avoid overshooting the budget.
MAX_SCENARIO_ROWS = 4


@dataclass
class Config:
    """Generation parameters — edit the defaults here or override them via the CLI."""

    NUM_EMPLOYEES: int = 10
    EVENTS_PER_EMPLOYEE: int = 3000
    START_TIME: datetime = field(default_factory=lambda: datetime.now(timezone.utc) - timedelta(hours=8))
    MOVE_EVENT_RATIO: float = 0.8
    OUTPUT_DIR: Path = Path("./data")
    SEED: int = 42


class Clock:
    """Monotonic timestamp source that advances by small random steps."""

    def __init__(self, start: datetime, rng: random.Random) -> None:
        self._now = start
        self._rng = rng

    def tick(self, low_ms: int = 40, high_ms: int = 2500) -> datetime:
        self._now += timedelta(milliseconds=self._rng.randint(low_ms, high_ms))
        return self._now

    @property
    def now(self) -> datetime:
        return self._now


class Archive:
    """Accumulates the per-file JSONL rows for one employee's archive."""

    FILES = ("windows.jsonl", "mouse.jsonl", "clipboard.jsonl", "ocr.jsonl")

    def __init__(self) -> None:
        self.files: dict[str, list[dict]] = {name: [] for name in self.FILES}
        self.ocr_seq = 0

    def add(self, filename: str, row: dict) -> None:
        self.files[filename].append(row)

    def count(self) -> int:
        return sum(len(rows) for rows in self.files.values())

    def move_count(self) -> int:
        return sum(1 for row in self.files["mouse.jsonl"] if row.get("event") == "move")

    def sort(self) -> None:
        for rows in self.files.values():
            rows.sort(key=lambda row: row["ts"])


# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text_box(rng: random.Random, text: str) -> tuple[int, int, int, int]:
    width = min(SCREEN_W - 20, 11 * max(4, len(text)))
    height = 28
    x = rng.randint(0, SCREEN_W - width - 1)
    y = rng.randint(0, SCREEN_H - height - 1)
    return (x, y, width, height)


def _center(box: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, width, height = box
    return (x + width // 2, y + height // 2)


def _move_row(ts: datetime, x: int, y: int) -> dict:
    return {"ts": _iso(ts), "event": "move", "mouse_x": x, "mouse_y": y}


def _click_row(ts: datetime, x: int, y: int, *, window_title: str | None, process_name: str | None) -> dict:
    return {
        "ts": _iso(ts),
        "event": "click",
        "button": "left",
        "mouse_x": x,
        "mouse_y": y,
        "click_count": "1",
        "window_title": window_title,
        "process_name": process_name,
    }


def _focus_row(ts: datetime, window_title: str, process_name: str) -> dict:
    return {
        "ts": _iso(ts),
        "event": "focus_change",
        "window_title": window_title,
        "process_name": process_name,
        "window_rect": [0, 0, SCREEN_W, SCREEN_H],
    }


def _clipboard_row(ts: datetime, text: str, source_app: str) -> dict:
    return {
        "ts": _iso(ts),
        "event": "clipboard_change",
        "clipboard_content_text": text,
        "clipboard_source_app": source_app,
    }


def _ocr_row(archive: Archive, ts: datetime, blocks: list[tuple[str, tuple[int, int, int, int]]]) -> dict:
    archive.ocr_seq += 1
    return {
        "ts": _iso(ts),
        "filename": f"frame_{archive.ocr_seq:05d}.jpg",
        "display_id": 0,
        "resolution": [SCREEN_W, SCREEN_H],
        "display_scale_factor": 1.0,
        "window_rect": [0, 0, SCREEN_W, SCREEN_H],
        "ocr_text_blocks": [{"text": text, "bounding_box": list(box), "confidence": 0.98} for text, box in blocks],
        "deduplicated_from": None,
    }


# --------------------------------------------------------------------------- #
# Rule-triggering scenarios (one per rule in rules.json)
# --------------------------------------------------------------------------- #

Scenario = Callable[[Archive, Clock, random.Random], None]


def _scenario_forwarded_email(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """forwarded-email-opened: click 'FW:*' with an Outlook process."""
    subject = rng.choice(["FW: Q3 numbers", "FW: Budget review", "FW: Weekly status", "FW: Signed contract"])
    box = _text_box(rng, subject)
    archive.add("ocr.jsonl", _ocr_row(archive, clock.tick(), [(subject, box), ("Inbox", _text_box(rng, "Inbox"))]))
    x, y = _center(box)
    archive.add(
        "mouse.jsonl",
        _click_row(clock.tick(), x, y, window_title="Inbox - Outlook", process_name=rng.choice(OUTLOOK_APPS)),
    )


def _scenario_urgent_email(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """urgent-email-opened: click '*urgent*' with an Outlook process."""
    subject = rng.choice(["Re: urgent: server down", "urgent: sign-off needed", "Please review (urgent)"])
    box = _text_box(rng, subject)
    archive.add("ocr.jsonl", _ocr_row(archive, clock.tick(), [(subject, box), ("Inbox", _text_box(rng, "Inbox"))]))
    x, y = _center(box)
    archive.add(
        "mouse.jsonl",
        _click_row(clock.tick(), x, y, window_title="Inbox - Outlook", process_name=rng.choice(OUTLOOK_APPS)),
    )


def _scenario_salesforce_delete(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """salesforce-record-deleted: click 'Delete' in a Salesforce window with a confirm dialog."""
    window = rng.choice(["Acme Lead | Salesforce", "Globex Opp | Salesforce", "Initech Account | Salesforce"])
    delete_box = _text_box(rng, "Delete")
    confirm = "Are you sure you want to delete this record?"
    archive.add("windows.jsonl", _focus_row(clock.tick(), window, "chrome.exe"))
    archive.add(
        "ocr.jsonl", _ocr_row(archive, clock.tick(), [("Delete", delete_box), (confirm, _text_box(rng, confirm))])
    )
    x, y = _center(delete_box)
    archive.add("mouse.jsonl", _click_row(clock.tick(), x, y, window_title=window, process_name="chrome.exe"))


def _scenario_jira_done(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """jira-ticket-done: click 'Done' in a Jira window."""
    window = f"PROJ-{rng.randint(100, 999)} - Jira - Agile Board"
    done_box = _text_box(rng, "Done")
    archive.add(
        "ocr.jsonl", _ocr_row(archive, clock.tick(), [("Done", done_box), ("Backlog", _text_box(rng, "Backlog"))])
    )
    x, y = _center(done_box)
    archive.add("mouse.jsonl", _click_row(clock.tick(), x, y, window_title=window, process_name="chrome.exe"))


def _scenario_invoice_clipboard(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """invoice-ready-to-attach: copy an 'INV-*' string from Outlook."""
    invoice = f"INV-{rng.randint(10000, 99999)}"
    archive.add("clipboard.jsonl", _clipboard_row(clock.tick(), invoice, rng.choice(OUTLOOK_APPS)))


SCENARIOS: tuple[Scenario, ...] = (
    _scenario_forwarded_email,
    _scenario_urgent_email,
    _scenario_salesforce_delete,
    _scenario_jira_done,
    _scenario_invoice_clipboard,
)


# --------------------------------------------------------------------------- #
# Noise + per-employee assembly
# --------------------------------------------------------------------------- #


def _add_noise_action(archive: Archive, clock: Clock, rng: random.Random) -> None:
    """Emit one actionable-but-irrelevant event (clicks on empty space, random focus, etc.)."""
    kind = rng.choice(("click", "focus", "clipboard", "ocr"))
    ts = clock.tick()
    if kind == "click":
        archive.add(
            "mouse.jsonl",
            _click_row(
                ts,
                rng.randrange(SCREEN_W),
                rng.randrange(SCREEN_H),
                window_title=rng.choice(NOISE_WINDOWS),
                process_name=rng.choice(NOISE_PROCESSES),
            ),
        )
    elif kind == "focus":
        archive.add("windows.jsonl", _focus_row(ts, rng.choice(NOISE_WINDOWS), rng.choice(NOISE_PROCESSES)))
    elif kind == "clipboard":
        archive.add("clipboard.jsonl", _clipboard_row(ts, rng.choice(NOISE_CLIPBOARD), rng.choice(NOISE_PROCESSES)))
    else:
        blocks = [(rng.choice(LOREM), _text_box(rng, "noise")) for _ in range(rng.randint(1, 3))]
        archive.add("ocr.jsonl", _ocr_row(archive, ts, blocks))


def build_employee(cfg: Config, employee_id: str, rng: random.Random) -> tuple[Archive, datetime, datetime]:
    """Generate one employee's events up to ``EVENTS_PER_EMPLOYEE`` rows."""
    start = cfg.START_TIME
    clock = Clock(start, rng)
    archive = Archive()
    target = cfg.EVENTS_PER_EMPLOYEE

    # Guarantee coverage: fire every rule once up front, then fill with noise + triggers.
    for scenario in SCENARIOS:
        scenario(archive, clock, rng)

    scenario_cycle = itertools.cycle(SCENARIOS)
    actionable_since_trigger = 0
    next_trigger_at = rng.randint(*TRIGGER_INTERVAL)

    while archive.count() < target:
        if rng.random() < cfg.MOVE_EVENT_RATIO:
            archive.add("mouse.jsonl", _move_row(clock.tick(), rng.randrange(SCREEN_W), rng.randrange(SCREEN_H)))
            continue

        actionable_since_trigger += 1
        inject = actionable_since_trigger >= next_trigger_at
        if inject and archive.count() + MAX_SCENARIO_ROWS <= target:
            next(scenario_cycle)(archive, clock, rng)
            actionable_since_trigger = 0
            next_trigger_at = rng.randint(*TRIGGER_INTERVAL)
        else:
            _add_noise_action(archive, clock, rng)

    archive.sort()
    return archive, start, clock.now


def clean_output_dir(output_dir: Path) -> None:
    """Remove existing emp-*.tar.gz files from output directory before generating new ones."""
    if output_dir.exists():
        for archive_file in output_dir.glob("emp-*.tar.gz"):
            try:
                archive_file.unlink()
                LOG.debug("Cleaned old archive: %s", archive_file.name)
            except Exception as err:
                LOG.warning("Failed to remove %s: %s", archive_file, err)


def write_archive(cfg: Config, employee_id: str, archive: Archive, start: datetime, end: datetime) -> Path:
    """Stage the JSONL files + metadata.json on disk, pack them into a ``.tar.gz``,
    then delete the staging tree so only the archive remains."""
    session_id = start.strftime("%Y%m%d_%H%M%S")
    staging = cfg.OUTPUT_DIR / f".staging_{employee_id}"
    session_dir = staging / session_id
    events_dir = session_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "schema_version": "1.0.0",
        "session_id": session_id,
        "employee_id": employee_id,
        "started_at": _iso(start),
        "ended_at": _iso(end),
        "machine": {
            "hostname": f"DESK-{employee_id}",
            "os_version": "Windows 11",
            "displays": [{"id": 0, "bounds": [0, 0, SCREEN_W, SCREEN_H], "scale": 1.0, "primary": True}],
        },
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    for name, rows in archive.files.items():
        body = "\n".join(json.dumps(row) for row in rows)
        (events_dir / name).write_text(body + ("\n" if body else ""), encoding="utf-8")

    archive_path = cfg.OUTPUT_DIR / f"{employee_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(session_dir, arcname=session_id)
    shutil.rmtree(staging)
    return archive_path


# --------------------------------------------------------------------------- #
# CLI + orchestration
# --------------------------------------------------------------------------- #


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def parse_args(cfg: Config, argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(description="Generate synthetic ContextPath event archives.")
    parser.add_argument("--employees", type=int, default=cfg.NUM_EMPLOYEES, help="number of employees to generate")
    parser.add_argument("--events", type=int, default=cfg.EVENTS_PER_EMPLOYEE, help="events per employee")
    parser.add_argument(
        "--move-ratio", type=float, default=cfg.MOVE_EVENT_RATIO, help="fraction of mouse-move noise [0..1]"
    )
    parser.add_argument("--output", type=Path, default=cfg.OUTPUT_DIR, help="output directory for the .tar.gz archives")
    parser.add_argument("--seed", type=int, default=cfg.SEED, help="PRNG seed for reproducibility")
    parser.add_argument("--start-hours-ago", type=float, default=None, help="override START_TIME to N hours before now")
    args = parser.parse_args(argv)

    start = cfg.START_TIME
    if args.start_hours_ago is not None:
        start = datetime.now(timezone.utc) - timedelta(hours=args.start_hours_ago)

    return Config(
        NUM_EMPLOYEES=args.employees,
        EVENTS_PER_EMPLOYEE=args.events,
        START_TIME=start,
        MOVE_EVENT_RATIO=args.move_ratio,
        OUTPUT_DIR=args.output,
        SEED=args.seed,
    )


def generate(cfg: Config) -> None:
    if not 0.0 <= cfg.MOVE_EVENT_RATIO < 1.0:
        raise SystemExit(f"--move-ratio must be in [0, 1), got {cfg.MOVE_EVENT_RATIO}")

    rng = random.Random(cfg.SEED)
    cfg.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    clean_output_dir(cfg.OUTPUT_DIR)

    LOG.info(
        "Generating %d employee(s) × %d events → %s (seed=%d)",
        cfg.NUM_EMPLOYEES,
        cfg.EVENTS_PER_EMPLOYEE,
        cfg.OUTPUT_DIR,
        cfg.SEED,
    )

    results: list[tuple[str, Path, int, int]] = []  # (employee, path, events, moves)
    for index in range(1, cfg.NUM_EMPLOYEES + 1):
        employee_id = f"emp-{index}"
        archive, start, end = build_employee(cfg, employee_id, rng)
        path = write_archive(cfg, employee_id, archive, start, end)
        events, moves = archive.count(), archive.move_count()
        results.append((employee_id, path, events, moves))
        LOG.info(
            "  %-7s %6d events (%4.1f%% move) → %s (%s)",
            employee_id,
            events,
            100 * moves / events,
            path.name,
            _human(path.stat().st_size),
        )

    total_events = sum(events for _, _, events, _ in results)
    total_bytes = sum(path.stat().st_size for _, path, _, _ in results)
    LOG.info("Done: %d archive(s), %d events total, %s on disk.", len(results), total_events, _human(total_bytes))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base_config = Config()
    final_config = parse_args(base_config, argv)
    generate(final_config)


if __name__ == "__main__":
    main()