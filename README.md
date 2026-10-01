# ContextPath

A small version of the ContextPath system: desktop recordings are replayed tick by tick,
evaluated against `rules.json`, and matching popups are stored and served over an audit API.

> This stub tracks the current skeleton.

## Quick start

```bash
cp .env.example .env   # adjust DATA_DIR / RULES_FILE if needed
make deploy            # docker compose up --build
```

The `server` service listens on `localhost:8080`:

```bash
curl 'http://localhost:8080/audit?employee=emp-1&rule=forwarded-email-opened'
```

## Services

| Service        | Role                                                            |
| -------------- | --------------------------------------------------------------- |
| `streamer`     | Replays one recording per employee, emits one-second ticks.     |
| `agent`        | Evaluates `rules.json` against ticks, emits popups.             |
| `popup-writer` | Persists popups to Redis cache and TimescaleDB.                 |
| `server`       | Serves `GET /audit?employee=<id>&rule=<id>` on port 8080.       |

Infrastructure: NATS (bus), Redis (cache), TimescaleDB (store).

## Local development

```bash
make open_project   # uv sync the workspace (uses PROJECT_PATH, defaults to .)
make test           # pytest across the workspace
make lint           # ruff
```

## Part 2

...
