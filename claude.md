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
- `streamer`: edge recorder replay. One asyncio task per `/data/*.tar.gz`. Streams tar members without unpacking to disk; k-way merge by `ts` into one-second ticks paced by `TICK_MS`. Publishes `ticks.`.
- `agent`: rule engine. One task per recording. Keeps screen/context state (focus window, clipboard, per-display OCR). Resolves `{click}` text by correlating click coordinates with OCR bounding boxes. Edge-triggered firing. Emits `RuleMatch` from engine, attaches `employee_id` and `ts`, then publishes `popups.detected`.
- `popup-writer`: subscribes to NATS subject `popups.detected` with queue group `popup-writers` and parses each message into the shared `Popup` model. Buffers popups and flushes in bulk when **either** `WRITER_BATCH_SIZE` is reached **or** `WRITER_FLUSH_INTERVAL` elapses. Writes popups **only to TimescaleDB** (the single source of truth), via batched `executemany` with `ON CONFLICT (employee, rule, ts) DO NOTHING` for at-least-once replay safety (idempotent). It does NOT write to Redis — the read cache is owned and populated by `server`. On `SIGINT`/`SIGTERM` it drains NATS, runs a final `writer.stop()` flush of the remaining buffer, then closes the DB pool (graceful shutdown, no data loss).
- `server`: serves `GET /audit?employee=&rule=` on port 8080. Read-through caching via `fastapi-cache2` with a Redis backend, TTL 60s: keyed queries (with `employee`) are cached; unkeyed queries bypass the cache and hit TimescaleDB directly to avoid caching heavy scans. Swagger UI is served at `/api` and the OpenAPI JSON at `/api/openapi.json`.

## Conventions

- Configuration comes from environment variables only (see `.env`). Atomic connection parameters (`HOST`, `PORT`, `USER`, etc.) assemble URLs dynamically via Pydantic `@computed_field`. No hardcoded endpoints.
- `popup_writer` batching knobs live in `shared/config.py`: `WRITER_BATCH_SIZE` (default `100`) — buffer size that triggers a flush; `WRITER_FLUSH_INTERVAL` (default `5.0` seconds) — timer that forces a flush of the remaining buffer.
- Code style is enforced via `ruff` and `make format` (pre-commit hooks).
- Do NOT index or read files inside `.venv/`, `.pytest_cache/`, `data/`, or `uv.lock` unless explicitly requested.
- Fail fast and log structured startup/shutdown events.
- Tests with `pytest` + `pytest-asyncio`. Run unit tests with `uv run pytest shared` or coverage via `uv run --with pytest-cov pytest shared --cov=shared --cov-report=term-missing`.