# GraphForge Local Demo Environment

A complete, local, GitHub-free demonstration of GraphForge: repository
indexing, tree-sitter parsing, the Neo4j knowledge graph, cross-repository
Kafka coupling, deterministic impact analysis, AI analysis, the Release
Coordination Plan, graph visualization, and the UI — all running against
four real local Spring Boot microservice repositories.

## Running it

```bash
scripts/demo-up.sh                      # starts Postgres + Neo4j + backend + frontend,
                                         # backend wired to the local demo repos instead of GitHub
cd backend && uv run python scripts/seed_demo.py   # registers, indexes, and analyzes everything
```

Then open http://localhost:5173 and sign in as `demo@graphforge.example.com` /
`correct-horse-battery-staple` (printed by the seed script, along with direct
links to each repository and scenario pull request).

`scripts/demo-up.sh` layers `docker/docker-compose.demo.yml` on top of the
normal dev stack — it mounts `demo/repositories/` into the backend container
read-only and sets `VCS_PROVIDER=local_git`, so "pull requests" resolve to
local git branches (see `LocalGitVersionControlProvider`) instead of hitting
the real GitHub API. Normal `scripts/docker-dev.sh` usage is completely
unaffected.

**AI analysis and the Release Coordination Plan need a real
`OPENAI_API_KEY`.** Set it in `backend/.env` (see `backend/.env.example`)
before running `scripts/demo-up.sh`. Without it, `seed_demo.py` still
completes — indexing, the graph, and deterministic analysis all work with
zero external dependencies — it just skips the AI step per scenario and
says so.

## 1. Repository architecture

Four independent, real git repositories under `demo/repositories/`, each with
its own multi-commit history on `main` (Initial project → domain model → REST
API → Kafka integration → refactor → bug fix) and its own `pom.xml`/
`application.yml`. Package layout is the same shape in all four:
`controller/ service/ repository/ dto/ entity/ events/ client/ config/
exception/ mapper/ util/`.

| Repository | Role | Kafka | Feign |
|---|---|---|---|
| **order-service** | Places and cancels orders | Producer: `order.created`, `order.cancelled` | Calls `payment-service` |
| **payment-service** | Charges and refunds payments | None | None (callee only) |
| **inventory-service** | Adjusts stock on order events | Consumer: `order.created`, `order.cancelled` | None |
| **notification-service** | Emails customers on order events | Consumer: `order.created`, `order.cancelled` | None |

Each service defines its **own local copy** of the Kafka event payload
classes (`OrderCreatedEvent`, `OrderCancelledEvent`) rather than sharing one
via a common library. This is deliberate, not an oversight: it means there is
no `MavenDependency` edge connecting the four repositories, and it means a
producer and consumer's idea of the event shape can silently drift apart —
exactly the risk a schema-breaking change should surface (see Scenario 1).

## 2. Cross-repository relationships

```
order-service --Feign(PaymentClient)--> payment-service        (same-repo edge only, never crosses repos)
order-service --produces--> "order.created"  <--consumes-- inventory-service
order-service --produces--> "order.created"  <--consumes-- notification-service
order-service --produces--> "order.cancelled" <--consumes-- inventory-service
order-service --produces--> "order.cancelled" <--consumes-- notification-service
```

Only the Kafka relationships are visible to GraphForge's cross-repository
analysis. This is a real, documented limitation of the current
implementation (not a demo simplification): `FeignClient.target_name` is
parsed and stored on the graph node but never matched against anything, so
**the Feign relationship never produces a cross-repository edge or a
cross-repository impact hop** — only Kafka topic-name string equality does
(see `Neo4jImpactGraphReader.find_cross_repository_topic_peers`). Scenario 2
demonstrates this directly.

## 3–6. Expected Neo4j graph — nodes and edges

Node labels are the graph's fixed allowlist: `Repository, Component,
Controller, Service, FeignClient, Endpoint, KafkaTopic, MavenDependency`.
Edge types: `CONTAINS, EXPOSES, CALLS, PRODUCES_TO, CONSUMES_FROM,
DEPENDS_ON`. `KafkaTopic` nodes are namespaced per repository — two repos
sharing a topic name get **two separate nodes**, linked only by the shared
`name` property (the Architecture page's "All repositories (merged)" view
deduplicates these client-side so the shared topic reads as one node).

**order-service**, after indexing (confirmed against a real indexed run):
- `Controller` — `OrderController`, with `Endpoint` nodes for
  `POST/GET/DELETE /orders...` (`EXPOSES`)
- `Service` — `OrderService` and `OrderEventPublisher` (both `@Service`-
  annotated). `OrderEventPublisher` additionally `PRODUCES_TO` →
  `KafkaTopic("order.created")` and `KafkaTopic("order.cancelled")` — a node
  can carry a stereotype label (`Service`) *and* a Kafka edge at the same
  time; "bare `Component`" only applies to a Kafka producer/consumer class
  that has *no* recognized stereotype annotation of its own (see
  inventory-service/notification-service's listeners below).
- `FeignClient` — `PaymentClient`, with its own `Endpoint` nodes for
  `POST /payments`, `GET /payments/{id}`, `POST /payments/{id}/refund`
  (`CALLS` — same-repo only, see §2)
- `MavenDependency` nodes for every `pom.xml` dependency (`DEPENDS_ON`)
- **Invisible to the graph** (by design, see the ground truth in the
  implementation plan): `OrderRepository` (JPA), `Order`/`OrderStatus`
  entities, all DTOs, `OrderNumberGenerator`, exception classes — none carry
  a recognized stereotype annotation

**payment-service**: `Controller` (`PaymentController` + its endpoints),
`Service` (`PaymentService`, `RefundService`), `MavenDependency` nodes. No
`KafkaTopic`, no `FeignClient` — matches its "No Kafka" design.

**inventory-service**: `Controller` (`InventoryController`), `Service`
(`InventoryService`), `Component` nodes for `OrderCreatedListener` /
`OrderCancelledListener` (each `CONSUMES_FROM` its topic), `MavenDependency`
nodes.

**notification-service**: `Controller` (`NotificationController`), `Service`
(`NotificationService`, `EmailService`), `Component` nodes for
`OrderCreatedListener` / `OrderCancelledListener` (`CONSUMES_FROM`),
`MavenDependency` nodes.

## 7. Deterministic risks this demo can produce

Per `risk_classifier.py`, only `HIGH`/`MEDIUM`/`LOW` exist (no "critical"
tier):

- **HIGH** — `pom.xml` changed, or a changed node produces/consumes a Kafka
  topic, or a changed node is a `FeignClient`. Scenarios 1, 2, and 4 all
  produce `HIGH`.
- **MEDIUM** — a changed `Controller` or `Service` with no Kafka/Feign/pom
  involvement. Any plain REST-endpoint change in any of the four repos
  demonstrates this.
- **LOW** — a change to a DTO, entity, exception, mapper, or util class with
  no graph-visible stereotype. Any change confined to those packages
  demonstrates this.

See `demo/scenarios/` for the four concrete walkthroughs.
