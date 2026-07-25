# Scenario 2 — Feign client API change

**Repository:** `order-service`, branch `pr-2`
**Change:** `PaymentClient` (the `@FeignClient(name = "payment-service")`
interface) gains a `refund(...)` method calling payment-service's real
`POST /payments/{id}/refund` endpoint, and `ChargeRequest` gains a
`currency` field.
**Files touched:** `client/PaymentClient.java`, `dto/ChargeRequest.java`,
`dto/RefundRequest.java` (new), `service/OrderService.java`.

## Expected deterministic analysis

- **Risk: `HIGH`** — `PaymentClient` is a directly-changed `FeignClient`
  node; the FeignClient rule fires independently of the Kafka rule.
- **Directly impacted:** `PaymentClient` (FeignClient), `OrderService`
  (Service).
- **Indirectly impacted (cross-repository): none.** This is the point of
  the scenario, not a gap to route around: Feign client `name=`/`value=` is
  parsed and stored on the graph node but **never matched against
  anything** (see `demo/DEMO_GUIDE.md` §2 and ADR-level ground truth in the
  implementation plan). Even though this change is obviously about
  `payment-service`, no edge crosses the repository boundary for Feign, so
  `payment-service` will not appear in `indirectly_impacted_services` no
  matter how obviously related it is.

## Expected AI analysis

- **Executive summary** should describe this as a change to order-service's
  own outbound contract toward payment-service's API — high risk to *this
  repository's* external calling behavior.
- **Breaking changes**: entry for `PaymentClient`, correctly scoped to
  "this service's Feign contract," not phrased as a payment-service change.
- **Release Coordination Plan should NOT invent a payment-service entry.**
  Since `payment-service` never appears in `impacted_repositories` for this
  PR (it isn't tracked as cross-repo impacted), `grounded_in()` will strip
  any attempt to notify or sequence it, and the single-repository
  `deployment_order` rule empties `deployment_order` entirely. If the AI
  output has a non-empty `deployment_order` or a `repositories_to_notify`
  entry naming `payment-service`, that's the enforcement working as
  designed, not a bug — the *raw* model output may try this, but the parsed
  result won't carry it through.

This is a deliberately honest scenario: GraphForge correctly flags real risk
to *this* repository without hallucinating a cross-repo relationship it
cannot actually prove from the graph.
