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
- Each service owns its `Dockerfile`, `pyproject.toml`, and `src//` package.
- Services depend on `cp-shared`; never duplicate shared models or the rule engine.

## Architecture

- CQRS. Write path: `streamer -> agent -> popup-writer`. Read path: `server`.
- `streamer`: edge recorder replay. One asyncio task per `/data/*.tar.gz`. Streams tar members without unpacking to disk; k-way merge by `ts` into one-second ticks paced by `TICK_MS`. Publishes `ticks.`.
- `agent`: rule engine. One task per recording. Keeps screen/context state (focus window, clipboard, per-display OCR). Resolves `{click}` text by correlating click coordinates with OCR bounding boxes. Edge-triggered firing. Emits `RuleMatch` from engine, attaches `employee_id` and `ts`, then publishes `popups.detected`.
- `popup-writer`: dual-write — synchronous Redis cache update plus batched `executemany` into TimescaleDB.
- `server`: serves `GET /audit?employee=&rule=` on port 8080, Redis first with TimescaleDB fallback.

## Conventions

- Configuration comes from environment variables only (see `.env`). Atomic connection parameters (`HOST`, `PORT`, `USER`, etc.) assemble URLs dynamically via Pydantic `@computed_field`. No hardcoded endpoints.
- Code style is enforced via `ruff` and `make format` (pre-commit hooks).
- Do NOT index or read files inside `.venv/`, `.pytest_cache/`, `data/`, or `uv.lock` unless explicitly requested.
- Fail fast and log structured startup/shutdown events.
- Tests with `pytest` + `pytest-asyncio`. Run unit tests with `uv run pytest shared` or coverage via `uv run --with pytest-cov pytest shared --cov=shared --cov-report=term-missing`.