# Engineering Decisions

## My Mistakes

Like any human, I make mistakes. For me, it is more important to explain why these mistakes happened rather than trying to hide them. I could have created a clean repository hiding the iterations, but that is not how professional developers operate. Ultimately, mistakes are an essential part of experience.

1. The commit history shows that the streaming service was initially designed differently. Based on my calculations, it should have performed well for up to 1,000 users. However, my performance benchmarks after commit 7 showed that service optimization was needed immediately. Therefore, I refactored it at this stage to ensure maximum throughput and speed.

2. Skipping database migrations in the MVP: Usually, migrations are added right away, but in this MVP, schema initialization is handled directly in code (repository.py with IF NOT EXISTS). TimescaleDB was chosen as an intermediate containerized solution for a fast start, while ClickHouse is the target data store for the long-term vision. Adding ORM migrations for a temporary Postgres instance felt redundant for an MVP.

---

## Accepted Decisions & Future Roadmap (Q&A)

### 1. Why Python for the MVP?

For the streaming service, I initially considered Go or Rust, but decided against overcomplicating the MVP since the performance gain would be negligible under the forced pacing rules.

Using Python: The async NATS Python client (nats-py) easily processes 20,000 to 50,000 events per second on a single instance, providing more than enough headroom for this MVP.
- Single Toolchain: A unified monorepo on uv workspace with a single linter (ruff), test suite (pytest), and shared Pydantic models in cp-shared without schema duplication.
- High Throughput: Async nats-py on asyncio handles 20,000-50,000 events/sec, far exceeding the requirements of this MVP.

**What's Next:** Rewrite the edge streamer in Go/Rust as a standalone desktop agent to eliminate GIL overhead and minimize resource usage (<15 MB RAM) on client machines.

---

### 2. How is the "1 streamer = 1 employee" requirement satisfied?

Independent async workers (asyncio.Task per archive) run concurrently in the MVP container, streaming data into isolated topics: events.raw.{employee_id}.

**What's Next:** Transition from containerized replayers to a decentralized OS-native binary installed directly on employee laptops (100,000 autonomous producers).

---

### 3. Why was TimescaleDB chosen for this MVP?

- Fast Batch Inserts: Hypertables provide automatic chunking by timestamp (ts) on top of standard asyncpg and SQLAlchemy 2.0.
- MVP Balance: Allows quick setup of a time-series database in Docker Compose with high write throughput without unnecessary infrastructure complexity.

| Criterion | PostgreSQL | ClickHouse | TimescaleDB (Postgres plugin) | Elasticsearch / OpenSearch |
| --- | --- | --- | --- | --- |
| Storage Type | Row (RDBMS) | Columnar (OLAP) | Hybrid (Time-series) | Inverted Index |
| Inserts | Medium | Extremely High | High (auto-chunking) | Medium / High |
| Analytics & Reports | Slow | Instant | Faster than Postgres (Hypertables) | Fast text aggregations |
| OCR Text Search | LIKE / pg_trgm | Full-text / Tokenized | pg_trgm | Full-text Search (FTS) |
| Compression | Minimal | 5x-10x | 2x-5x | Low |

**What's Next:** Migrate the analytical store to ClickHouse for columnar compression, sub-second reporting, and fast queries over billions of event rows across multiple B2B tenants.

---

### 4. Why NATS?

- Out-of-the-box Autoscaling: NATS Queue Groups automatically distribute incoming ticks when scaling agents (docker compose scale agent=5).
- Decoupling: Fully isolates streamer, agent, and popup_writer services.
- Foundation for NATS JetStream: Allows enabling persistence and partitioning by employee_id without changing message schemas or message brokers.
- Efficient Protocol & Flexible Topology: Uses lightweight NATS Core Protocol (over TCP) with support for NATS Leaf Nodes for local edge collection.

**What's Next:** Configure NATS JetStream subject partitioning by employee_id to guarantee event ordering per employee across replica scaling, and deploy NATS Leaf Nodes for regional latency reduction.

---

### 5. Why a separate popup_writer service?

CQRS Pattern: Write operations significantly outweigh reads. Separating write paths ensures batch database writes do not block event tick processing.

**What's Next:** Scale `popup_writer` replicas independently using Kubernetes HPA driven by NATS consumer queue lag metrics.

---

### 6. Why a Read-Through Cache (Redis) for the server?

- The exact API load and query frequency for /audit were unknown beforehand. Instead of requesting additional assumptions, I chose a defensive design approach.
- Database Protection: A read-through cache with a 60s TTL guarantees response times < 1ms for repeated keyed requests and protects TimescaleDB from load spikes.

**What's Next:** Upgrade to Redis Cluster for high availability and implement active cache invalidation via NATS events (`rules_updated.{client_id}`) when rules are updated via Web UI.

---

### 7. Why CQRS with NATS + Redis + TimescaleDB instead of direct HTTP?

Direct HTTP calls from agents would block the tick processing pipeline under heavy load. The asynchronous bus fully decouples components for non-blocking operation.


**What's Next:** Expose OpenTelemetry distributed tracing headers across the entire path (Agent -> NATS -> popup_writer -> DB) for complete request visibility in Jaeger.

---

### 8. Performance optimizations for the streamer service:

- Lazy K-Way Merge (heapq.merge): Line-by-line reading from .tar.gz files in memory using generators. Merging K event logs per employee happens on-the-fly with O(K) memory instead of loading the entire O(N) dataset into RAM.
- Byte-level Pre-filtering: High-frequency non-actionable events (e.g. mouse movement "event": "move") are filtered out at the substring/byte level before calling json.loads() or Pydantic validation.
- Delta-Time Fast-Forwarding: Skips empty seconds without events without artificial sleep delays. Sleep duration is calculated only between actual event timestamps (delta_seconds * TICK_MS), keeping total replay time predictable (~1.5-2 mins) without wasting CPU on empty ticks.
- NATS Batching: Buffers tick events locally and dispatches them in batches via publish_batch, eliminating network round-trip overhead per event.

**What's Next:** Implement local SQLite/LevelDB offline event buffering on the desktop agent to hold events locally during network outages and flush upon reconnect.

---

### 9. How is RuleEngine and Click-to-OCR matching optimized?

- Pre-compiled Regex: All text patterns in rules.json are compiled into re.Pattern instances once during RuleEngine initialization rather than per event.
- Spatial Click ↔ OCR Correlation (O(blocks)): Mouse click coordinates (x, y) are mapped only to active display bounds and OCR blocks containing that point. This avoids full-screen scanning per click.
- Infix Search + Whitespace Normalization: OCR uses re.search with whitespace normalization (\s+) so multi-line OCR text correctly matches patterns like *Are you sure...*.

**What's Next:** Abstract rule loading into an Adapter/Strategy pattern to support custom rule dialects per B2B tenant and enable dynamic hot-reloading without container restarts.

---

### 10. How is idempotency and write reliability handled in popup_writer?

- Idempotent Writes (ON CONFLICT DO NOTHING): The popups table uses a composite unique constraint (employee, rule, ts). Duplicate tick replay or network retries cannot insert duplicate rows.
- Batch Flushes (executemany): Popups are buffered asynchronously and written in single bulk SQL transactions, minimizing WAL/database overhead.

**What's Next:** Replace runtime schema initialization (`IF NOT EXISTS`) with versioned, idempotent database migration scripts managed via Alembic or Goose.

---

## Domain Invariants, Edge Cases & Known Trade-offs

To strike a balance between MVP delivery speed and code reliability, several intentional design choices and domain invariants were defined:

### 1. Infix Matching vs. Strict Literal Spec for OCR
- **Decision:** OCR pattern matching uses infix evaluation (`re.search` without `^...$`), whereas `click`, `process`, and `window_title` use full-string anchor matching.
- **Rationale:** Strict full-string matching for OCR would prevent rules like `salesforce-record-deleted` ("Are you sure you want to delete*") from ever matching, as screen OCR text naturally contains surrounding layout text.

### 2. Event Trigger Architecture (Action-Driven Evaluation)
- **Decision:** Rule evaluations (`MatchContext`) are generated upon `click` and `clipboard` events, using the latest `focus` and `ocr` states as background context.
- **Rationale:** All current operational rules are triggered by user actions. Treating high-frequency window/screen updates strictly as context minimizes redundant rule engine evaluations.

### 3. Input Data Preconditions & Invariants
- **Chronological Stream Sorting:** Each `.jsonl` stream inside the archive is assumed to be pre-sorted by `ts`. Inter-stream merging relies on a memory-efficient $O(K)$ `heapq.merge`.
- **Archive Mapping:** The system enforces a strict invariant of 1 `.tar.gz` archive per unique `employee_id`.
- **Backward OCR Deduplication:** `deduplicated_from` resolution assumes backward-referencing canonical frames containing populated text blocks.

### 4. Display & State Retention Simplifications
- **Coordinate Scaling:** Spatial click-to-OCR matching assumes a 1:1 coordinate space (`scale = 1.0`). HiDPI scaling dynamically adjusts via display bounds in production.
- **State Expiry:** Context state (e.g., active clipboard and screen text) remains cached until overwritten by subsequent events of the same type without explicit TTL decay.

### 5. Stream Replay vs. Database Idempotency
- **Decision:** Idempotency is enforced at the database layer using `UNIQUE(employee, rule, ts)` with `ON CONFLICT DO NOTHING`.
- **Known Trade-off:** Replaying an archive or retrying network bursts ensures zero database duplicate entries, but the worker still re-evaluates rules for previously processed ticks. Stream offset tracking and deduplication at ingestion are deferred to JetStream consumer offsets in Production.