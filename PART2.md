# Part 2

To release the system, I divide development among four product teams and specify the key architectural enhancements for each.

---

## Team Topology

* **Desktop Agent Team (Go/Rust):** Cross-platform desktop client (Windows/macOS), system resource optimization, local capture/OCR, and a reliable auto-update mechanism.
* **Backend & Platform Team (K8s, NATS, ClickHouse):** High-throughput ingestion, event bus, storage scaling, rule evaluation, monitoring, and SLA compliance.
* **Frontend & B2B Integrations Team (FastAPI, React):** Rule management web panel for client administrators, multi-tenancy, RBAC, and integrations with HR/Security systems.
* **AI & Security Research Team (ML/AI):** Behavioral anomaly detection models, local classification of sensitive data (PII/Source Code), and security rule generation.

---

## 1. Desktop Agent Team

* **Decentralized Desktop Agent (Go/Rust / Edge Architecture):** Offloading capture logic to employee PCs as a lightweight binary (RAM < 15 MB, no GIL). 100,000 devices become autonomous producers, completely removing the load from the streaming backend.
* **Resource Management & Auto-Throttling:** Dynamic control of system load. When running on battery or during peak CPU usage, the agent automatically reduces OCR frequency and frame capture rates, remaining invisible to the user.
* **Offline Buffering & Fault Tolerance:** Local event queue (SQLite/LevelDB) on the desktop to preserve ticks during network outages, with transparent delivery to NATS once connectivity is restored.
* **Safe Release Cycle (Canary Rings):** Auto-updates via release rings (Ring 0 — internal environment, Ring 1 — Canary 1–5% with Sentry tracking, Ring 2 — full release) to protect clients from deployment failures.
* **Proven Go/Rust Core Engine:** Reuses the zero-drift Go streaming architecture proven in this MVP as the core event-loop and ingestion engine for the cross-platform desktop agent binary.

---

## 2. Backend & Platform Team

* **Migration to ClickHouse:** Replacing TimescaleDB with a columnar data store that provides extreme data compression and sub-second analytics over billions of events.
* **Kubernetes (K8s) Orchestration:** Deploying microservices with HPA configuration. The `agent` and `popup_writer` workers scale based on NATS queue lag, while `server` scales based on incoming RPS.
* **NATS JetStream Partitioning by `employee_id`:** Assigning an employee's events to a specific worker. Guarantees chronological event order and correct operation of edge-triggered rules under scaling.
* **Distributed NATS & Leaf Nodes:** Geo-distributed NATS deployment with desktops connecting via TCP to the nearest node to minimize network RTT.
* **Batch Archive Processing via S3 and Work Queue:** Uploading `.tar.gz` archives to S3/MinIO with task distribution via NATS Work Queue with At-Least-Once delivery guarantees.
* **Migrations (Alembic/Goose) and Redis Cluster:** Database schema versioning and Redis clustering to eliminate single points of failure in the cache layer.
* **Observability, Tracing & CI/CD:** Structured JSON logging (`structlog`), end-to-end OpenTelemetry/Jaeger tracing, and CI/CD pipelines with automated tests and Trivy vulnerability scanning.
* **SLA/SLO Policies and On-call:** Guaranteed SLO (popup latency < 50 ms, API availability 99.9%), Prometheus/Grafana dashboards, and Alertmanager + PagerDuty for on-call duties.
* **Stream Offset Management & Deduplication:** Implementation of NATS JetStream Consumer Offsets and KV state storage to track the last processed timestamp (`ts`) per employee. This allows agents to skip already-evaluated ticks on restarts, avoiding redundant CPU/IO execution.
* **Historical Rule Re-evaluation Strategy:** Designing an explicit re-evaluation pipeline for historical logs when `rules.json` changes, allowing B2B admins to choose between backfilling updated popup contents or keeping existing historical audit logs intact.

---

## 3. Frontend & B2B Integrations Team

* **Rule Management and Hot-Reload:** Web panel for `rules.json`. Publishing `rules_updated.{client_id}` to NATS invalidates the Redis cache and hot-reloads rules in worker memory without restarting containers.
* **Multi-tenancy and Data Isolation:** Isolation at the ClickHouse schema level and separate Kubernetes namespaces for each client to comply with GDPR and SOC2 requirements.
* **UX/UI Design and Analytics Dashboards:** B2B interface for security officers with employee activity heatmaps, rule trigger dynamics, and software usage metrics.
* **SIEM and HR System Integrations:** Webhook module and pre-built connectors (Splunk, QRadar, Workday, BambooHR) for exporting popups and audit logs directly into the customer's ecosystem.

---

## 4. AI & Security Research Team

* **Behavioral Anomaly Detection (Unsupervised ML):** Training online models (Isolation Forest / Autoencoders) on action history to track anomalies without explicit rules.
* **Smart PII and Secret Classification (Edge NLP):** Integration of ultra-lightweight ML models (ONNX / TFLite) into the desktop agent for instant detection of passports, credit cards, and API keys in clipboard and OCR.
* **LLM Rule Generator (Natural Language to Rules):** In-UI assistant translating security officers' text requirements (e.g., *"Warn when passwords are entered into ChatGPT"*) into structured `rules.json`.
* **Semantic Screen Analysis:** Compact computer vision models for semantic recognition of critical UI elements ("Delete", "Export" buttons, upload forms) instead of binding to rigid OCR coordinates.


--- 


## Critical Scope for 10-Client Release and Timeline Estimates

Critical for MVP (Must-Have):
- **Decentralized Desktop Agent (Go/Rust):** Offloading streaming logic from the backend to employee PCs, basic offline buffering, and a safe auto-update mechanism (Canary Rings). Without this, the central backend will not sustain thousands of devices.
- **Multi-tenancy and Data Isolation:** Strict segregation of data across 10 organizations at the storage schema level and K8s namespaces to maintain baseline security and prevent cross-tenant data leaks.
- **Migration to ClickHouse + NATS JetStream:** Replacing the temporary database with ClickHouse to sustain high write workloads, and partitioning NATS by `employee_id` to preserve tick ordering.
- **Basic Rule Management UI with Hot-Reload:** Web interface for security administrators supporting "hot-reloading" of `rules.json` without restarting worker containers.
- **Basic K8s, CI/CD, and Observability:** Autoscaling (HPA), structured JSON logging, and NATS queue lag monitoring with alerting.

**Requirement Flexibility:** I consider it important to note that the final scope of critical features can adapt to specific B2B client needs. For example, if a client requires an exclusively On-Premise deployment within their isolated perimeter instead of SaaS, priorities will shift toward simplifying installation Helm charts, while integrations with local SIEM/HR systems or ML anomaly detection models will be deferred to phase two.

---

### My Estimation of Timelines and Development Deadlines

Assuming parallel execution across four established teams, the approximate target deadline to reach pilot readiness for 10 clients is **13–14 weeks (around 3–3.5 months)**.*

- **Phase 1 (Weeks 1–4) — Discovery & Foundation:** Gathering and refining technical requirements with initial client IT/Security teams (OS constraints, proxy/firewall environment), freezing Protobuf contracts, developing the core Go agent, migrating schema to ClickHouse, and setting up a multi-tenant NATS cluster.
- **Phase 2 (Weeks 5–8) — Services & UI:** JetStream partitioning, developing the B2B rule management panel (Hot-Reload), implementing desktop update Canary Rings, and configuring K8s HPA.
- **Phase 3 (Weeks 9–12) — Hardening & Release:** Load benchmarks on 10,000+ emulated agents, configuring Prometheus/Grafana alerts, end-to-end tracing, and launching Ring 1 (Canary 1–5%) on pilot desktop endpoints of the first clients.
- **Phase 4 (Weeks 13–14) — Product Pilot Testing:** Testing with the first client + addressing feedback, edge cases, and emerging issues.

*This buffer is included to account for potential issues that may surface during the pilot rollout.