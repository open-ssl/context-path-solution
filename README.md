# ContextPath

Desktop activity recordings are replayed tick-by-tick, evaluated against `rules.json`, and
every matching popup is persisted and served over an audit API. The system is a CQRS pipeline
built as a `uv` workspace monorepo: a shared library (`cp-shared`) plus four services wired
together by NATS, Redis, and TimescaleDB.

## Quick start

```bash
cp .env.example .env         # adjust DATA_DIR / RULES_FILE for your dataset
make run                     # docker compose up --build -d  (infra + 4 services)
```

Query the audit API (`server` listens on `localhost:8080`):

```bash
curl 'http://localhost:8080/audit?employee=emp-1&rule=forwarded-email-opened'
```

Both query params are optional; each popup is returned as `{employee, rule, ts, title, body}`.
Swagger UI is at `http://localhost:8080/api`.

```bash
make down      # stop the stack        make test   # uv run pytest --cov
make logs      # follow service logs    make lint   # ruff check + format
```

## Services

| Service        | Role                                                                    |
| -------------- | ----------------------------------------------------------------------- |
| `streamer`     | Lazily replays `.tar.gz` recordings (K-way merge by `ts`), emits per-employee `events.raw.{id}` batches. |
| `agent`        | Evaluates `rules.json` per employee, emits popups to `popups.detected`. |
| `popup_writer` | Batches popups into TimescaleDB (idempotent, source of truth).          |
| `server`       | Serves `GET /audit?employee=<id>&rule=<id>` on port 8080.               |


## Data & NATS flow

From a raw recorded event to a row in TimescaleDB and out through `/audit`:

```
.tar.gz archives (one per employee)
  metadata.json  ─┐
  events/*.jsonl ─┤  lazy line-stream (extractfile)
                  ▼
┌──────────┐ events.raw.{emp}  ┌────────┐   NATS               ┌──────────────┐
│ streamer │ ═══ (batches) ══▶ │ agent  │ ── popups.detected ─▶│ popup_writer │
└──────────┘                   └────────┘   (Popup)            └──────────────┘
  one worker per archive         subscribes events.raw.>         batched executemany
  K-way heapq.merge by ts        AgentManager: per-employee      ON CONFLICT DO NOTHING
  byte-filter "move" events      RuleEngine (edge-triggered)            │
  delta-time fast-forward        click↔OCR correlation                 ▼
  per-tick NATS batching                                        ┌──────────────┐
                                                                 │ TimescaleDB  │  (source of truth,
                                                                 └──────────────┘   hypertable on ts)
                                                                        ▲
                               GET /audit?employee=&rule=              │ read-through
                        ┌────────┐   cache miss (TTL 60s)              │
          client ─────▶ │ server │ ───────────────────────────────────┘
                        └────────┘ ──▶ Redis (fastapi-cache2)  cache hit
```

- **Subjects:** `streamer → events.raw.{employee_id} → agent (events.raw.> wildcard) → popups.detected → popup_writer`.
- **Message models (shared):** per-tick JSON-array batches of `RawEvent{employee, ts, MatchContext}`
  on each `events.raw.{employee_id}`; `Popup{employee, rule, ts, title, body}` on `popups.detected`.
- **Write vs read:** `popup_writer` writes **only** TimescaleDB; `server` owns the Redis cache.

## Target architecture (Docker Compose)

```
                         contextpath-network (bridge)
┌───────────────────────────────────────────────────────────────────────────┐
│  Infrastructure                                                             │
│    nats:2-alpine (-js)   redis:7-alpine   timescale/timescaledb:pg16        │
│                                                                             │
│  Services (multistage uv images, non-root)                                  │
│    streamer ──▶ nats                      [restart: no  — runs to completion]│
│    agent    ──▶ nats              (reads /rules.json, RULES_FILE=/rules.json)│
│    popup_writer ──▶ nats, timescaledb                                       │
│    server   ──▶ redis, timescaledb        [ports: 8080 → host]              │
└───────────────────────────────────────────────────────────────────────────┘
  streamer mounts the dataset read-only at /data (DATA_DIR=/data)
  depends_on uses healthchecks; services start only once infra is healthy
```

Configuration is environment-only. Connection endpoints are passed as **atomic parts**
(`NATS_HOST/PORT`, `REDIS_HOST/PORT/DB`, `POSTGRES_HOST/PORT/USER/PASSWORD/DB`) and composed
into URLs/DSNs inside `shared/config.py` via Pydantic `@computed_field`; the host defaults
(`nats`, `redis`, `timescaledb`) match the Compose service names. `TICK_MS`, `DATA_DIR`, and
`RULES_FILE` are passed/overridden per service.

## Load & resource envelope

Single-host resource envelope for the take-home dataset (3 employees, ~14k raw events across archives). Below are both the **real benchmarked metrics** (measured under streaming workload + API load) and the initial **design target caps**.

### Measured Benchmark (Actual Run)

Measured via `scripts/benchmark.py` during streaming replay (~1m 45s total replay duration) under concurrent **100 RPS load** on `GET /audit`:

| Component    | CPU (peak) | RAM (peak) | Notes                                                 |
| ------------ | ---------- | ---------- | ----------------------------------------------------- |
| streamer     | 0.08 vCPU  | ~98 MB     | One worker per archive; lazy line-stream, O(K) merge. |
| agent        | <0.05 vCPU | ~32 MB     | Pre-compiled regex engine, per-employee state.        |
| popup_writer | 0.05 vCPU  | ~32 MB     | Batched `executemany`; buffer bounded by batch.       |
| server       | 0.13 vCPU  | ~52 MB     | FastAPI + Redis read-through cache.                   |
| nats         | 0.01 vCPU  | ~25 MB     | Core NATS messaging queue.                            |
| redis        | 0.02 vCPU  | ~10 MB     | Hot `/audit` responses (60s TTL).                     |
| timescaledb  | 0.01 vCPU  | ~131 MB    | Hypertable on `ts`.                                   |

### Target Envelope (Initial Design Caps)

Original single-host estimates and resource allocation caps prior to benchmarking:

| Component    | CPU (target) | RAM (target) | Notes                                                 |
| ------------ | ------------ | ------------ | ----------------------------------------------------- |
| streamer     | 0.2 vCPU     | ~128 MB      | One worker per archive; lazy line-stream, O(K) merge. |
| agent        | 0.3 vCPU     | ~128 MB      | Pre-compiled regex engine, per-employee state.        |
| popup_writer | 0.2 vCPU     | ~128 MB      | Batched `executemany`; buffer bounded by batch.       |
| server       | 0.3 vCPU     | ~192 MB      | FastAPI + Redis read-through cache.                   |
| nats         | 0.1 vCPU     | ~64 MB       | Core NATS messaging queue.                            |
| redis        | 0.1 vCPU     | ~64 MB       | Hot `/audit` responses.                               |
| timescaledb  | 0.5 vCPU     | ~512 MB      | Hypertable on `ts`.                                   |


**Redis read-through — ~60× DB load reduction.** `/audit` responses are cached with a 60-second
TTL, so a hot key is read from TimescaleDB at most once per 60 s. A sustained **100 RPS** of
repeated/keyed reads therefore collapses to **~1.6 RPS** at the database (`100 / 60 ≈ 1.6`) — a
~60× reduction — while p99 latency drops to Redis round-trips on cache hits. Unkeyed (no
`employee`) scans deliberately bypass the cache to avoid caching heavy full-table reads.


### Streamer Execution Logs

Below is the verified container output demonstrating parallel worker execution, byte-filtering (~14k raw lines reduced to 2,758 actionable events), and clean termination. 

> **Note:** A full visual snippet of this container execution output is available at [`docs/img/streamer-benchmark-log.png`](docs/img/streamer-benchmark-log.png).

```text
2026-10-02 12:20:30,608 streamer INFO streamer starting
2026-10-02 12:20:30,608 streamer.services.archive INFO discovered 3 archive(s) in /data
2026-10-02 12:20:30,619 streamer.services.archive INFO opened archive for emp-1 (emp-1.tar.gz)
2026-10-02 12:20:30,628 streamer.services.archive INFO opened archive for emp-2 (emp-2.tar.gz)
2026-10-02 12:20:30,635 streamer.services.archive INFO opened archive for emp-3 (emp-3.tar.gz)
2026-10-02 12:20:30,649 streamer.core.bus INFO connected to NATS at nats://nats:4222
2026-10-02 12:22:13,271 streamer.services.replay INFO replay complete for emp-3: published 879 RawEvent(s)
2026-10-02 12:22:15,292 streamer.services.replay INFO replay complete for emp-2: published 745 RawEvent(s)
2026-10-02 12:22:15,303 streamer.services.replay INFO replay complete for emp-1: published 1134 RawEvent(s)
2026-10-02 12:22:15,304 streamer INFO streamer finished: 2758 RawEvent(s) published under events.raw.*
2026-10-02 12:22:15,304 streamer INFO streamer stopped
