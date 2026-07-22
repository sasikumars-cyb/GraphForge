# Scenario 1 — Breaking Kafka schema change

**Repository:** `order-service`, branch `pr-1`
**Change:** `OrderCreatedEvent.total` (`BigDecimal`, dollars) renamed to
`totalCents` (`long`, cents) — both a field rename and a type change, a
classic "avoid floating point rounding" refactor with no compiler-enforced
contract against the two consumers deserializing this same event.
**Files touched:** `events/OrderCreatedEvent.java`,
`events/OrderEventPublisher.java` — the topic name (`"order.created"`) is
unchanged, so cross-repository topic-name matching still fires.

## Expected deterministic analysis

- **Risk: `HIGH`** — the changed node (`OrderEventPublisher`) already
  produces to a Kafka topic in the indexed graph; the "topic touched" rule
  fires regardless of what specifically changed inside the event class.
- **Directly impacted:** `OrderEventPublisher` (Component).
- **Indirectly impacted (cross-repository), confirmed against a real run:
  all four listeners across both topics** —
  `inventory-service`'s and `notification-service`'s `OrderCreatedListener`
  *and* `OrderCancelledListener`, not just the two `OrderCreatedListener`s.
  This is a real, worth-explaining nuance, not noise: impact analysis is
  **file-level**, not field- or topic-level (see `SourceLocation` — line
  numbers are never populated). `OrderEventPublisher.java` produces to
  *both* `order.created` and `order.cancelled`, so once that file is
  identified as directly changed, every topic it touches — and everyone
  downstream of each of those topics — is reported as impacted, even
  though only the `order.created` payload actually changed. A demo
  walkthrough should call this out explicitly rather than let it look like
  a bug.
- **Impacted topics:** both `order.created` and `order.cancelled`, for the
  same reason.

## Expected AI analysis

- **Executive summary** should name the field rename/type change explicitly
  and state that it changes the wire shape of `order.created`.
- **Breaking changes**: one entry for `OrderEventPublisher`/`order.created`,
  severity high, grounded in the deterministic evidence above (never
  inventing a repository not present in "Impacted Repositories").
- **Migration advice**: update both consumers to read `totalCents` (long)
  instead of `total` (BigDecimal) before or alongside this deploy.

## Expected Release Coordination Plan

Since **two** distinct downstream repositories are genuinely impacted, this
is the one scenario where a real `deployment_order` should appear:

```json
{
  "deployment_order": [
    {"order": 1, "repository": "inventory-service", "action": "Deploy the tolerant totalCents reader first", "reason": "Consumes order.created and would misread the old total field otherwise"},
    {"order": 2, "repository": "notification-service", "action": "Deploy the tolerant totalCents reader", "reason": "Consumes order.created and would misread the old total field otherwise"},
    {"order": 3, "repository": "order-service", "action": "Deploy the producer once both consumers are ready", "reason": "Only safe to publish the new shape after consumers no longer expect the old one"}
  ],
  "repositories_to_notify": [
    {"repository": "inventory-service", "reason": "Consumes order.created and reads the renamed/retyped field", "urgency": "blocking"},
    {"repository": "notification-service", "reason": "Consumes order.created and reads the renamed/retyped field", "urgency": "blocking"}
  ],
  "rollout_risks": ["Deserialization or null-field errors in inventory-service and notification-service if the producer ships before they do"]
}
```

The exact wording is model-generated and will vary; what should **not**
vary is that both `repositories_to_notify` entries are `blocking`, and that
no repository outside `{order-service, inventory-service,
notification-service}` ever appears (`grounded_in()` strips anything else).
