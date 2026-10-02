# ContextPath — Engineering Guidelines

## Language

- All code, comments, docstrings, commit messages, and documentation MUST be in English.
- Add comments only when they explain non-obvious intent. No restating what the code already says.

## Stack

The system is polyglot: the hot-path replay engine is Go; everything else is Python.

- **Go 1.22+** — the `streamer` service (`services/streamer`, Go module `contextpath/streamer`):
  - `goroutines` for per-archive parallelism; `time.Ticker` for replay pacing.
  - `archive/tar` + `compress/gzip` for streaming archive reads; `container/heap` for the K-way merge.
  - Official NATS Go client `github.com/nats-io/nats.go` for publishing.
  - Standard library JSON (`encoding/json`) for the wire contract.
- **Python 3.12**, `async`/`await` throughout — `agent`, `popup_writer`, `server`, and the `shared` library:
  - Pydantic v2 for models and validation; `pydantic-settings` for configuration.
  - NATS via `nats-py` for the message bus.
  - Redis via `redis` (async client) for the O(1) popup read cache.
  - TimescaleDB via `asyncpg` for the time-series popup store.
  - FastAPI + Uvicorn for the read API (`server`).
  - `uv` workspace monorepo for the Python packages.

## Repository

- Mixed monorepo: a `uv` workspace for the Python side plus a standalone Go module for the streamer.
- **Python (`uv` workspace):** shared logic lives in `shared/`; each Python service is a workspace member under `services/*`. The distribution package is `cp-shared`, imported in code as `shared` (via `packages = ["src/shared"]`). Services depend on `cp-shared`; never duplicate shared models or the rule engine.
  - Standard layered layout inside every Python service's `src/<name>/` package:
    - `api/` — routers and HTTP endpoints (FastAPI `APIRouter`).
    - `core/` — cross-cutting setup (cache backend, clients, app wiring).
    - `db/` — connection pools, DDL/migrations, and repositories (SQL lives here).
    - `services/` — business logic (orchestration, caching decorators).
    - `main.py` — assembly point only: builds the app, manages the lifespan, wires routers. No business logic.
- **Go (`services/streamer`):** a self-contained module, **excluded from the `uv` workspace** (`[tool.uv.workspace] exclude = ["services/streamer"]`) so `uv sync` never treats it as a Python package. Layout:
  - `Dockerfile` — multi-stage build (`golang:1.22-alpine` builder → minimal `alpine` runtime with `ca-certificates`, non-root user, `-ldflags="-s -w"`).
  - `go.mod`, `go.sum`, `main.go` (orchestration + graceful `os.Exit(0)`).
  - `internal/` packages:
    - `model` — wire types (`RawEvent`, `MatchContext`) and decoded source events; field names/null semantics match the Python Pydantic models.
    - `config` — environment loading (`NATS_HOST`/`NATS_PORT`, `DATA_DIR`, `TICK_MS`).
    - `archive` — streaming `tar`/`gzip` reader, K-way merge, OCR dedup.
    - `geometry` — click → OCR bounding-box correlation.
    - `replay` — per-employee state machine, tick batching, pacing.
    - `bus` — NATS connect (retry) and batch publish.
- Root `--project-directory .` is used for all Docker Compose and build contexts. The `streamer` build context is the repo root (`dockerfile: services/streamer/Dockerfile`).

## Architecture

- CQRS. Write path: `streamer (Go) -> agent (Python) -> popup_writer (Python)`. Read path: `server (Python)`.
- Data flow (write path): `streamer` publishes raw events to per-employee NATS subjects `events.raw.{employee_id}` → `agent` subscribes the `events.raw.>` wildcard, runs each through the rule engine, and publishes matches to NATS `popups.detected` → `popup_writer` batches them into `TimescaleDB` → `server` serves reads from `TimescaleDB` (with a Redis read-through cache).
- The Go/Python boundary is the `events.raw.*` wire contract only: `streamer` emits JSON-array batches of `RawEvent` (`employee`, `ts`, `MatchContext`) that the Python `agent` decodes unchanged (`RawEventBatch` TypeAdapter). No shared code crosses the boundary — only the JSON schema.

### Streamer engine (Go)

High-throughput log-replay service. Compose service name `streamer` (container `contextpath-streamer`).

- **Parallelism:** one worker **goroutine per `/data/*.tar.gz` archive** (one archive per employee), all launched concurrently and joined via `sync.WaitGroup` over a shared `*nats.Conn`. Goroutines are scheduled across available cores (`runtime.GOMAXPROCS`); the container's core budget is set by `STREAMER_CPUS` (Compose `deploy.resources.limits.cpus`, default `2`).
- **Lazy K-way merge:** each archive is read **lazily** — never loaded whole into memory. `metadata.json` (→ `employee_id` + displays) is read up front, then each relevant `events/*.jsonl` member (`windows`→focus, `mouse`→clicks, `clipboard`, `ocr`) is streamed line by line through `archive/tar` + `compress/gzip` (a fresh handle per member). The per-file chronological streams are merged by `ts` through a min-heap priority queue (`container/heap`), holding at most one row per open file — **O(K) memory** for K files. Mouse-move events are rejected by a raw **byte-substring pre-filter** (`"event": "move"`) before JSON decoding. OCR `deduplicated_from` is resolved on the fly (running map of first-seen `ocr_text_blocks`; canonical frames precede their references).
- **Pacing & timers:** the one-second recording tick is the unit of both batching and pacing. A `time.Ticker` built from `TICK_MS` (default `25ms`, tunable down to ~10–15ms) drives **delta-time fast-forward** — between two populated seconds the worker consumes `delta` ticker beats, so idle gaps collapse to a single proportional pause with near-zero scheduling drift; same-second events never pause. One tick's actionable events ship as a single NATS message (also capped at a 200-event batch).
- **MatchContext building:** clicks resolve `{click}` text by correlating coordinates with OCR bounding boxes (`geometry`), clipboard changes carry the copied text, focus/OCR update running per-employee state (screen text is the concatenation of current OCR blocks across displays, in first-seen order).
- **NATS topic safety:** `employee_id` is sanitized for the subject (dots `.` and spaces replaced with `_`) when publishing to `events.raw.{employee_id}`; the original id is preserved in the `RawEvent` payload (what the agent stores). `NATS_URL` is composed inside the worker from `NATS_HOST`/`NATS_PORT`, mirroring the Python `shared` config.
- **Resource profile:** ultra-low footprint — ~18 MB RAM for 100 concurrent employee workers.
- **Lifecycle:** runs to completion (`restart: "no"`), logs per-worker start/progress (every 100 events)/completion with timing and a final total with throughput, then flushes NATS and exits `os.Exit(0)`. Malformed JSONL lines are skipped.

### Other services (Python)

- `agent`: owns the rule engine. Subscribes to the `events.raw.>` wildcard (covering every per-employee subject), decoding each message as a batch of `RawEvent`s. An `AgentManager` routes events to a **per-employee** isolated `RuleEngine` (a separate edge-triggered state machine per `employee_id`), created lazily on first sight, so firing never leaks across employees. For every `RuleMatch` it builds a `Popup` (carrying `employee`, `rule`, `ts`, rendered `title`/`body`) and publishes it to `popups.detected`.
- `popup_writer`: subscribes to NATS subject `popups.detected` with queue group `popup-writers` and parses each message into the shared `Popup` model. Buffers popups and flushes in bulk when **either** `WRITER_BATCH_SIZE` is reached **or** `WRITER_FLUSH_INTERVAL` elapses. Writes popups **only to TimescaleDB** (the single source of truth), via batched `executemany` with `ON CONFLICT (employee, rule, ts) DO NOTHING` for at-least-once replay safety (idempotent). It does NOT write to Redis — the read cache is owned and populated by `server`. On `SIGINT`/`SIGTERM` it drains NATS, runs a final `writer.stop()` flush of the remaining buffer, then closes the DB pool (graceful shutdown, no data loss).
- `server`: serves `GET /audit?employee=&rule=` on port 8080 (both params optional). Read-through caching via `fastapi-cache2` with a Redis backend, TTL 60s: keyed queries (with `employee`) are cached; unkeyed queries bypass the cache and hit TimescaleDB directly to avoid caching heavy scans. Swagger UI is served at `/api` and the OpenAPI JSON at `/api/openapi.json`.

## Rule Engine & Matching

The rule engine lives in `shared` (Python) and is owned by the `agent`.

- Rules use the native `rules.json` schema: each rule has `id`, a `when` block (per-field conditions over `click`, `process`, `window_title`, `clipboard`, `ocr`), and a `popup` template (`title`/`body`). A condition is a single pattern or a list (list matches if any element matches); a `null`/missing condition is ignored. `{field}` placeholders in the popup template are substituted from the matched context.
- Patterns treat only `*` as special (any run of characters), are case-insensitive, and are **pre-compiled** to `re.Pattern` once per rule (at `RuleEngine` construction) so hot-path evaluation of NATS events does no per-event regex compilation.
- Matching is field-aware:
  - `click`, `process`, `window_title`, `clipboard` — **anchored** whole-string match (`^…$`), so `olk.exe` never matches `folk.exe`.
  - `ocr` — **infix** match via `re.search` (the phrase may appear anywhere in the screen text), with whitespace normalization first: runs of whitespace and newlines (`\s+`) collapse to single spaces and the text is trimmed. This makes an OCR phrase matchable regardless of how it was split across blocks/lines or padded, without requiring leading/trailing `*` in the pattern.
- Firing is **edge-triggered per employee** (see `agent`): a rule fires on the transition into "satisfied" and re-arms once the condition clears.
- The Go `streamer` reproduces the same click↔OCR geometry and context-building so the `MatchContext` it emits is identical to what the Python engine expects.

## Conventions

- Configuration comes from environment variables only (see `.env`). For the Python services, atomic connection parameters (`HOST`, `PORT`, `USER`, etc.) assemble URLs dynamically via Pydantic `@computed_field`; the Go `streamer` composes its NATS URL from the same `NATS_HOST`/`NATS_PORT` atomic parts. No hardcoded endpoints.
- `STREAMER_CPUS` (default `2`) caps the Go streamer's CPU cores via Compose `deploy.resources.limits.cpus`. `TICK_MS` (default `25`) sets real milliseconds per one-second recording tick.
- `popup_writer` batching knobs live in `shared/config.py`: `WRITER_BATCH_SIZE` (default `100`) — buffer size that triggers a flush; `WRITER_FLUSH_INTERVAL` (default `5.0` seconds) — timer that forces a flush of the remaining buffer.
- Code style: Python is enforced via `ruff` and `make format` (pre-commit hooks); Go via `gofmt` and `go vet`.
- Do NOT index or read files inside `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `data/`, `uv.lock`, or Go build caches unless explicitly requested.
- Fail fast and log structured startup/shutdown events.

## Testing & Coverage

- **Python** — tests use `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`). Run with `uv run pytest`, or with coverage via `uv run pytest --cov --cov-report=term-missing`.
  - **Hermetic by design — no external services.** DB repositories, the NATS bus, and the Redis cache are exercised through injected async fakes / `AsyncMock`-style doubles and `monkeypatch`, never a live Postgres/NATS/Redis:
    - DB pools: `asyncpg.create_pool` is monkeypatched to a fake pool (fake connection records executed DDL / forwarded query args), covering `init_pool` success, retry, and exhaustion, plus `run_migrations`, `get_pool`, `close_pool`, and `fetch_audits`.
    - NATS: `bus.connect` is tested by monkeypatching `nats.connect` (success / retry / exhaustion).
    - Cache: `redis.asyncio.from_url` and `FastAPICache.init` are monkeypatched so `init_cache`/`close_cache` run without Redis. HTTP endpoint tests use `httpx.ASGITransport` (skips lifespan) with an in-memory `fastapi-cache` backend.
  - **Randomized / property-based tests** guard the matching and geometry logic against arbitrary input:
    - `test_matching.py` — fuzzes random noise around a target phrase to assert infix OCR matching holds.
    - `test_geometry.py` — randomized click coordinates inside/outside OCR bounding boxes (and outside all displays).
  - Coverage targets the business logic and core services (rule engine, matching, geometry, DB repositories, cache, bus); the uncovered remainder is the service `main.py` entry points (`asyncio.run` / signal / FastAPI-lifespan wiring), which are integration-level rather than unit-tested.
- **Go (`streamer`)** — validated with `gofmt`, `go vet`, and `go build` (run in the `golang:1.22-alpine` image). Behavioural compatibility is verified end-to-end: the unchanged Python `agent` decodes 100% of the Go batches (zero malformed drops) and all rules fire, confirming the wire contract matches.
