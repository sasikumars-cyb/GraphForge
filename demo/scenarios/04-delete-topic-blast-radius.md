# Scenario 4 — Delete the OrderCancelled topic

**Repository:** `order-service`, branch `pr-4`
**Change:** `OrderEventPublisher.publishOrderCancelled(...)` no longer calls
`kafkaTemplate.send("order.cancelled", ...)` — the method is kept (so
`OrderService.cancelOrder`'s call site needs no change) but becomes a no-op,
representing the retirement of the `order.cancelled` event entirely.
**Files touched:** `events/OrderEventPublisher.java` only.

## Expected blast radius

The deterministic engine analyzes this PR against the **already-indexed**
`main` graph, where `OrderEventPublisher` still has a `PRODUCES_TO` edge to
`order.cancelled` — that's exactly what makes this scenario meaningful: the
analysis reflects "what currently depends on this producer," not the
post-change state.

- **Risk: `HIGH`** — the changed node already produces to a Kafka topic.
- **Directly impacted:** `OrderEventPublisher`.
- **Indirectly impacted (cross-repository), i.e. the blast radius: all four
  listeners across both topics**, confirmed against a real run —
  `inventory-service`'s and `notification-service`'s
  `OrderCancelledListener` *and* `OrderCreatedListener`. As in Scenario 1,
  this is file-level analysis: `OrderEventPublisher.java` still produces to
  `order.created` too, and touching the file at all reports every topic it
  touches as impacted, not only the one whose producer call was actually
  removed. The two `OrderCancelledListener` results are the real blast
  radius; the two `OrderCreatedListener` results are a false-positive
  side-effect of file-level (not call-level) granularity worth calling out
  in a demo rather than glossing over.

## Expected AI output

- **Executive summary** should explicitly frame this as removing a producer
  that two other repositories currently depend on — "will silently break
  inventory-service's and notification-service's order-cancellation
  handling" rather than a generic "Kafka producer removed" statement.
- **Breaking changes**: entry for `OrderEventPublisher`, high severity,
  reasoning citing the two real consumers by name (grounded in
  `indirectly_impacted_services`, not invented).
- **Release Coordination Plan**: `repositories_to_notify` should list both
  `inventory-service` and `notification-service` as `blocking` (their
  cancellation-handling logic is dead code the moment this ships), and
  `rollout_strategy`/`rollout_risks` should recommend the safe order —
  update or retire the two consumers' cancellation handling *before* or
  *alongside* removing the producer, not after, since there is no
  compiler error to catch a consumer silently waiting forever for an event
  that will never arrive again.

This is the mirror image of Scenario 1: instead of a payload shape changing
under two consumers, the producer disappears entirely — the same
topic-name-matching mechanism that finds who's *affected by a schema
change* also finds who's *currently relying on something being deleted*.
