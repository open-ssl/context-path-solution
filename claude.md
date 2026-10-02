# ContextPath — Engineering Guidelines

## Language

- All code, comments, docstrings, commit messages, and documentation MUST be in English.
- Add comments only when they explain non-obvious intent. No restating what the code already says.

## Stack

- Python 3.12, `async`/`await` throughout.
- Pydantic v2 for models and validation; `pydantic-settings` for configuration.
- NATS via `nats-py` for the message bus.
- Redis via `redis` (async client) for the O(1) popup cache and worker state.
- TimescaleDB via `asyncpg` for the time-series popup store.
- FastAPI + Uvicorn for the read API (`server`).

## Repository

- uv workspace monorepo. Shared logic lives in `shared/`; each service is a workspace member under `services/*`.
- Package distribution name is `cp-shared`, imported in Python code as `shared` (configured via `packages = ["src/shared"]`).
- Root `--project-directory .` is used for all Docker Compose and build contexts.
- Each service owns its `Dockerfile`, `pyproject.toml`, and `src/<name>/` package.
- Services depend on `cp-shared`; never duplicate shared models or the rule engine.
- Standard layered layout inside every service's `src/<name>/` package:
  - `api/` — routers and HTTP endpoints (FastAPI `APIRouter`).
  - `core/` — cross-cutting setup (cache backend, clients, app wiring).
  - `db/` — connection pools, DDL/migrations, and repositories (SQL lives here).
  - `services/` — business logic (orchestration, caching decorators).
  - `main.py` — assembly point only: builds the app, manages the lifespan, wires routers. No business logic.

## Architecture

- CQRS. Write path: `streamer -> agent -> popup-writer`. Read path: `server`.
- Data flow (write path): `streamer` publishes raw events to NATS `events.raw` → `agent` runs them through the rule engine and publishes matches to NATS `popups.detected` → `popup-writer` batches them into `TimescaleDB` → `server` serves reads from `TimescaleDB` (with a Redis read-through cache).
- `streamer`: edge-recorder replay engine. Discovers `/data/*.tar.gz` (one archive per employee) and unpacks each **in memory** (`tarfile` over `BytesIO`, never written to disk), reading `metadata.json` (→ `employee_id` + displays) and the relevant `events/*.jsonl` (`windows`→focus, `mouse`→clicks, `clipboard`, `ocr`). OCR `deduplicated_from` is resolved by reusing the referenced frame's `ocr_text_blocks`. All employees' events are merged into one chronological stream (sorted by `ts`), grouped into one-second ticks; between consecutive ticks it sleeps `TICK_MS` to emulate recording pacing. For each actionable event it builds a `MatchContext` — clicks resolve `{click}` text by correlating coordinates with OCR bounding boxes; clipboard changes carry the copied text; focus/OCR update running state — and publishes a `RawEvent` (`employee`, `ts`, `MatchContext`) to `events.raw`. When all archives are exhausted it returns (graceful stop) and drains NATS. Malformed JSONL lines are skipped.
- `agent`: owns the rule engine. Subscribes to `events.raw`, parses each message into a `RawEvent` (employee + ts + `MatchContext`), and evaluates it. State is isolated **per employee**: each `employee_id` gets its own `RuleEngine` instance so edge-triggered firing never leaks across employees. For every `RuleMatch` it builds a `Popup` (carrying `employee`, `rule`, `ts`, rendered `title`/`body`) and publishes it to `popups.detected`.
- `popup-writer`: subscribes to NATS subject `popups.detected` with queue group `popup-writers` and parses each message into the shared `Popup` model. Buffers popups and flushes in bulk when **either** `WRITER_BATCH_SIZE` is reached **or** `WRITER_FLUSH_INTERVAL` elapses. Writes popups **only to TimescaleDB** (the single source of truth), via batched `executemany` with `ON CONFLICT (employee, rule, ts) DO NOTHING` for at-least-once replay safety (idempotent). It does NOT write to Redis — the read cache is owned and populated by `server`. On `SIGINT`/`SIGTERM` it drains NATS, runs a final `writer.stop()` flush of the remaining buffer, then closes the DB pool (graceful shutdown, no data loss).
- `server`: serves `GET /audit?employee=&rule=` on port 8080. Read-through caching via `fastapi-cache2` with a Redis backend, TTL 60s: keyed queries (with `employee`) are cached; unkeyed queries bypass the cache and hit TimescaleDB directly to avoid caching heavy scans. Swagger UI is served at `/api` and the OpenAPI JSON at `/api/openapi.json`.

## Rule Engine & Matching

- Rules use the native `rules.json` schema: each rule has `id`, a `when` block (per-field conditions over `click`, `process`, `window_title`, `clipboard`, `ocr`), and a `popup` template (`title`/`body`). A condition is a single pattern or a list (list matches if any element matches); a `null`/missing condition is ignored. `{field}` placeholders in the popup template are substituted from the matched context.
- Patterns treat only `*` as special (any run of characters), are case-insensitive, and are **pre-compiled** to `re.Pattern` once per rule (at `RuleEngine` construction) so hot-path evaluation of NATS events does no per-event regex compilation.
- Matching is field-aware:
  - `click`, `process`, `window_title`, `clipboard` — **anchored** whole-string match (`^…$`), so `olk.exe` never matches `folk.exe`.
  - `ocr` — **infix** match via `re.search` (the phrase may appear anywhere in the screen text), with whitespace normalization first: runs of whitespace and newlines (`\s+`) collapse to single spaces and the text is trimmed. This makes an OCR phrase matchable regardless of how it was split across blocks/lines or padded, without requiring leading/trailing `*` in the pattern.
- Firing is **edge-triggered per employee** (see `agent`): a rule fires on the transition into "satisfied" and re-arms once the condition clears.

## Conventions

- Configuration comes from environment variables only (see `.env`). Atomic connection parameters (`HOST`, `PORT`, `USER`, etc.) assemble URLs dynamically via Pydantic `@computed_field`. No hardcoded endpoints.
- `popup_writer` batching knobs live in `shared/config.py`: `WRITER_BATCH_SIZE` (default `100`) — buffer size that triggers a flush; `WRITER_FLUSH_INTERVAL` (default `5.0` seconds) — timer that forces a flush of the remaining buffer.
- Code style is enforced via `ruff` and `make format` (pre-commit hooks).
- Do NOT index or read files inside `.venv/`, `.pytest_cache/`, `data/`, or `uv.lock` unless explicitly requested.
- Fail fast and log structured startup/shutdown events.

## Testing & Coverage

- Tests use `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`). Run the suite with `uv run pytest`, or with coverage via `uv run pytest --cov --cov-report=term-missing`.
- **Hermetic by design — no external services.** DB repositories, the NATS bus, and the Redis cache are exercised through injected async fakes / `AsyncMock`-style doubles and `monkeypatch`, never a live Postgres/NATS/Redis:
  - DB pools: `asyncpg.create_pool` is monkeypatched to a fake pool (fake connection records executed DDL / forwarded query args), covering `init_pool` success, retry, and exhaustion, plus `run_migrations`, `get_pool`, `close_pool`, and `fetch_audits`.
  - NATS: `bus.connect` is tested by monkeypatching `nats.connect` (success / retry / exhaustion).
  - Cache: `redis.asyncio.from_url` and `FastAPICache.init` are monkeypatched so `init_cache`/`close_cache` run without Redis. HTTP endpoint tests use `httpx.ASGITransport` (skips lifespan) with an in-memory `fastapi-cache` backend.
- **Randomized / property-based tests** guard the matching and replay logic against arbitrary input:
  - `test_matching.py` — fuzzes random noise around a target phrase to assert infix OCR matching holds.
  - `test_archive.py` — randomized OCR `deduplicated_from` chains, plus broken/empty JSONL, duplicate canonical frames, and directory members.
  - `test_replay.py` — randomized click coordinates inside/outside OCR bounding boxes (and outside all displays), plus an end-to-end OCR-noise → rule-fires check.
- **Coverage:** 100% of the business logic and core services (rule engine, matching, replay/archive, DB repositories, cache, bus). Project-wide coverage is ~84%; the uncovered remainder is the service `main.py` entry points (`asyncio.run` / signal / FastAPI-lifespan wiring), which are integration-level rather than unit-tested.