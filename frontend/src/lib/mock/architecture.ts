import type { ServiceNode } from "../../types/domain";

/** Sample data only — no backend is connected yet; no graph engine runs here. */
export const mockServiceNodes: ServiceNode[] = [
  {
    id: "svc-order",
    name: "order-service",
    repository: "order-service",
    dependents: 2,
    dependencies: 1,
    risk: "low",
  },
  {
    id: "svc-payment",
    name: "payment-service",
    repository: "payment-service",
    dependents: 3,
    dependencies: 0,
    risk: "critical",
  },
  {
    id: "svc-notification",
    name: "notification-service",
    repository: "notification-service",
    dependents: 0,
    dependencies: 2,
    risk: "medium",
  },
  {
    id: "svc-partner-webhook",
    name: "partner-webhook",
    repository: "partner-webhook",
    dependents: 0,
    dependencies: 1,
    risk: "high",
  },
  {
    id: "svc-customer",
    name: "customer-service",
    repository: "customer-service",
    dependents: 1,
    dependencies: 0,
    risk: "high",
  },
];
