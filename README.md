# ContextPath

Desktop activity recordings are replayed tick-by-tick, evaluated against `rules.json`, and
every matching popup is persisted and served over an audit API. The system is a CQRS pipeline
built as a `uv` workspace monorepo: a shared library (`cp-shared`) plus four services wired
together by NATS, Redis, and TimescaleDB.

## Quick Start

### Prerequisites

```bash
cp .env.example .env
```

> **Rules:** `rules.json` mounts dynamically — modify rules on the fly without rebuilds. Default matches the task spec.

---

### Option 1 — Docker Compose Directly (Default)

```bash
docker compose up --build
```

Uses ready-made archives in `./data/` (`emp-1.tar.gz` – `emp-3.tar.gz`). Zero setup needed.

### Option 2 — Makefile (`make run`)

```bash
make run
```

Auto-detects environment (`uv` / `poe` or system `python3`). Runs `docker compose up --build -d` with readiness checks.

### Option 3 — Fresh Data + Run (`make gen-run`)

```bash
make gen-run
```

Generates a synthetic dataset (10 employees × 3,000 events) into `./data/` via `dev/scripts/generate_data.py` (triggers all 5 rules), then launches the stack.

---

Query the audit API — `server` on `localhost:8080`, Swagger at `/api` (both params optional):

```bash
curl 'http://localhost:8080/audit?employee=emp-1&rule=forwarded-email-opened'
```

### MVP Performance (10 employees, 100 RPS on `/audit`)

**Resources:**

| Component      | CPU (peak) | RAM (peak) |
| -------------- | ---------- | ---------- |
| `streamer`     | ~0.13 vCPU | ~31 MB     |
| `agent`        | ~0.05 vCPU | ~29 MB     |
| `popup_writer` | ~0.05 vCPU | ~32 MB     |
| `server`       | ~0.38 vCPU | ~48 MB     |
| `timescaledb`  | —          | ~133 MB    |

**Speed:** 10 archives (3049 events) replay in **~1m 38s – 1m 45s** — "one hour in 1–2 minutes".

Full 3-vs-100-employee breakdown under [Load & resource envelope](#load--resource-envelope).

### Further Reading

- **[DECISIONS.md](DECISIONS.md)** — architecture decisions & trade-offs.
- **[PART2.md](PART2.md)** — scaling to the commercial version.

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

Single-host resource envelope and performance metrics. Below are real benchmarked metrics captured under full streaming workload across different dataset scales (**3 vs 100 concurrent employee workers**) while under a sustained **100 RPS load** on `GET /audit`.

### Performance & Processing Time Summary

| Dataset Scale | Total Raw Events | Total Wall-Clock Time | Avg Worker Time / Employee | Effective Throughput (Wall-Clock / Emp) |
| :--- | :--- | :--- | :--- | :--- |
| **3 Employees** | 2,758 events | **1m 45s** (105s) | ~1m 44s | ~35.0s / employee |
| **100 Employees** | 21,039 events | **1m 42s** (102s) | ~1m 41s | **~1.02s / employee** (~58.8 emp/min) |

> **Key takeaway on concurrency:** Processing **100 employees** (21,000+ events) takes the **exact same total wall-clock time (~1m 42s)** as processing 3 employees (~1m 45s). Thanks to asynchronous event loop scheduling and lazy $O(K)$ stream merging, scaling worker load by **33×** kept memory usage flat with zero CPU throttling.

---

### Measured Resource Benchmarks (3 vs 100 Employees)

Measured via `scripts/benchmark.py` during streaming replay under **100 RPS audit API load**:

| Component    | CPU Peak (3 emp) | RAM Peak (3 emp) | CPU Peak (100 emp) | RAM Peak (100 emp) | Architecture Notes                                    |
| :----------- | :--------------- | :--------------- | :----------------- | :----------------- | :---------------------------------------------------- |
| streamer     | 0.08 vCPU        | ~98 MB           | 0.12 vCPU          | ~73 MB             | One worker per archive; lazy line-stream, O(K) merge. |
| agent        | <0.05 vCPU       | ~32 MB           | 0.05 vCPU          | ~28 MB             | Pre-compiled regex engine, per-employee state.        |
| popup_writer | 0.05 vCPU        | ~32 MB           | 0.05 vCPU          | ~32 MB             | Batched `executemany`; buffer bounded by batch.       |
| server       | 0.13 vCPU        | ~52 MB           | 0.13 vCPU          | ~49 MB             | FastAPI + Redis read-through cache.                   |
| nats         | 0.01 vCPU        | ~25 MB           | 0.04 vCPU          | ~13 MB             | Core NATS messaging queue.                            |
| redis        | 0.02 vCPU        | ~10 MB           | 0.01 vCPU          | ~10 MB             | Hot `/audit` responses (60s TTL).                     |
| timescaledb  | 0.01 vCPU        | ~131 MB          | 0.02 vCPU          | ~101 MB            | Hypertable on `ts`.                                   |

---

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

---

### Streamer Execution Logs (3 and 100 Employees Run)

Below is the verified container output for the 3-employee baseline run, demonstrating parallel worker execution and clean termination (~1m 45s total duration):
> **Note:** A full visual snippet of this container execution output is available at [`docs/img/streamer-benchmark-log.png`](docs/img/streamer-benchmark-log.png).

Below is a truncated output showing 100 parallel workers running concurrently, publishing 21,039 events to NATS in **1m 42s** (avg ~1.02s wall time per employee), and terminating cleanly.

```text
2026-10-02 13:17:30,100 streamer INFO streamer starting
2026-10-02 13:17:30,105 streamer.services.archive INFO discovered 100 archive(s) in /data
2026-10-02 13:17:30,120 streamer.services.archive INFO opened archive for emp-1 (emp-1.tar.gz)
...
2026-10-02 13:17:30,350 streamer.services.archive INFO opened archive for emp-100 (emp-100.tar.gz)
2026-10-02 13:17:30,380 streamer.core.bus INFO connected to NATS at nats://nats:4222
...
2026-10-02 13:19:11,520 streamer.services.replay INFO replay complete for emp-2: published 210 RawEvent(s)
2026-10-02 13:19:11,552 streamer.services.replay INFO replay complete for emp-86: published 212 RawEvent(s)
...
2026-10-02 13:19:12,763 streamer.services.replay INFO replay complete for emp-60: published 219 RawEvent(s)
2026-10-02 13:19:12,884 streamer.services.replay INFO replay complete for emp-95: published 212 RawEvent(s)
2026-10-02 13:19:12,885 streamer INFO streamer finished: 21039 RawEvent(s) published under events.raw.*
2026-10-02 13:19:12,886 streamer INFO streamer stopped
```


## Performance & Resource Envelope (Python vs. Go Streamer)

### 1. Resource Consumption Profile (Single-Host Envelope)
Measured baseline envelope under streaming workload and 100 RPS audit API load:

| Component | CPU (peak) | RAM (peak) | Infrastructure Role |
| :--- | :--- | :--- | :--- |
| **streamer (Go)** | **0.08 vCPU** | **~18 MB** | 100 goroutine workers, streaming `tar.Reader` ($O(K)$ merge) |
| streamer (Python) | 0.18 vCPU | ~89 MB | Single-threaded `asyncio` worker pool |
| **agent** | 0.09 vCPU | ~29 MB | Pre-compiled regex engine, per-employee state |
| **popup_writer** | 0.05 vCPU | ~32 MB | Batched `executemany`, bounded buffer |
| **server** | 0.13 vCPU | ~48 MB | FastAPI + Redis read-through cache |
| **nats** | 0.06 vCPU | ~14 MB | Core NATS message broker |
| **redis** | 0.02 vCPU | ~10 MB | Hot `/audit` cache (60s TTL) |
| **timescaledb** | 0.01 vCPU | ~101 MB | Hypertable time-series store |

---

### 2. Streamer Benchmark: Python vs. Go (100 Employees / 103k Events)

| Metric | Python Streamer (`asyncio`) | Go Streamer (`goroutines`) | Advantage / Notes |
| :--- | :--- | :--- | :--- |
| **CPU Allocation** | 1.0 Core (GIL Limit) | 2.0 Cores (`STREAMER_CPUS`) | True multi-core parallelism |
| **Execution Wall-Clock**| 5m 25.00s (325s) | **5m 22.09s (322s)** | Matches theoretical pacing limit |
| **Timer Drift / Lag** | +2.91s lag | **0.00s lag** | Zero scheduling drift |
| **Throughput (RPS)** | ~317.6 events/sec | **~320.5 events/sec** | Strictly paced by `TICK_MS=25` |
| **Peak RAM Usage** | ~89 MB - 250 MB | **~18 MB** | **~80% Memory Reduction** |
| **Concurrency** | 1 OS Thread / 100 Tasks | 100 Goroutines / OS Threads | $M:N$ Scheduler efficiency |

---

### 3. Scaling `TICK_MS` Pacing Limits (100 Employees Dataset)

With zero scheduler drift in Go, total replay time scales linearly with `TICK_MS`:

| `TICK_MS` Value | Total Replay Duration | System Throughput |
| :--- | :--- | :--- |
| `TICK_MS = 25` (Default) | **5m 22s** (322s) | ~320 events/sec |
| `TICK_MS = 10` | **2m 08s** (128s) | ~805 events/sec |
| `TICK_MS = 1` (Burst Mode) | **~12.8s** | **~8,060+ events/sec** |