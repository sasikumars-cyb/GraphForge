# Scenario 3 — New Kafka consumer

**Repository:** `inventory-service`, branch `pr-3`
**Change:** adds `OrderShippedListener`, a new `@KafkaListener(topics =
"order.shipped", groupId = "inventory-service")`, plus its
`OrderShippedEvent` payload class — added ahead of any producer, since no
service in this demo currently publishes to `order.shipped`.
**Files touched:** `events/OrderShippedEvent.java` (new),
`events/OrderShippedListener.java` (new).

## Expected graph changes

Before this PR is indexed, inventory-service's graph has two `KafkaTopic`
nodes (`order.created`, `order.cancelled`) and two `Component`→
`CONSUMES_FROM`→`KafkaTopic` edges. After re-indexing `pr-3`:

- A **new `KafkaTopic("order.shipped")` node** appears, namespaced to
  inventory-service (`{repository_id}:kafka-topic:order.shipped`).
- A **new `Component` node** for `OrderShippedListener` (bare `Component`,
  not `Service`/`Controller` — it has no recognized stereotype annotation
  of its own, it's discovered purely because it declares a `@KafkaListener`
  method).
- A **new `CONSUMES_FROM`** edge from that component to the new topic node.

## Why the deterministic risk comes back `LOW`, not `HIGH`

This is the scenario's real teaching point, confirmed against a live run:
the deterministic analysis for this PR comes back **risk `LOW`, with an
empty `directly_impacted_services` list** — not `HIGH`, even though a Kafka
topic is clearly involved.

The reason is exactly how impact analysis works: `find_nodes_by_file_paths`
looks up the changed file paths against the graph indexed from `main`
*before* this PR. `OrderShippedEvent.java` and `OrderShippedListener.java`
are **brand new files** — they don't exist in that pre-PR graph at all, so
there is no existing node for them to match against, `direct_nodes` comes
back empty, and every risk rule (`pom_changed`, `topics_touched`,
Controller/Service) has nothing to evaluate. This is a genuine, documented
limitation (impact analysis works off one indexed snapshot, not a true
before/after graph diff) rather than a demo quirk — a brand-new file adding
a Kafka consumer is invisible to risk classification until it's indexed
once and then changed *again* in a later PR.

## What re-indexing after merge shows

Once `pr-3` is merged and `inventory-service` is re-indexed, the graph
gains a **new `KafkaTopic("order.shipped")` node** (namespaced to
inventory-service) and a **new bare `Component` node** for
`OrderShippedListener` (no `Service`/`Controller` stereotype — discovered
purely because it declares a `@KafkaListener` method), linked by a new
`CONSUMES_FROM` edge. `find_cross_repository_topic_peers` still finds
**no** cross-repository peer for `order.shipped` at this point: no
repository in this demo produces to it yet. The link only materializes
once a real producer exists somewhere with the exact same topic string
literal — if order-service later adds
`kafkaTemplate.send("order.shipped", ...)`, the next analysis run on either
side finds the other automatically, no code change to the matching logic
required, since it's a pure property-based join on `KafkaTopic.name`.
