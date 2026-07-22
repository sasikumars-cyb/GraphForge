import type { Repository } from "../../types/domain";

/** Sample data only — no backend is connected yet. */
export const mockRepositories: Repository[] = [
  {
    id: "repo-order",
    name: "order-service",
    provider: "GitHub",
    services: 1,
    openPullRequests: 2,
    health: "healthy",
    lastAnalyzed: "2026-07-21T09:00:00Z",
  },
  {
    id: "repo-payment",
    name: "payment-service",
    provider: "GitHub",
    services: 1,
    openPullRequests: 1,
    health: "critical",
    lastAnalyzed: "2026-07-21T09:12:00Z",
  },
  {
    id: "repo-notification",
    name: "notification-service",
    provider: "GitHub",
    services: 1,
    openPullRequests: 1,
    health: "attention",
    lastAnalyzed: "2026-07-20T16:44:00Z",
  },
  {
    id: "repo-partner-webhook",
    name: "partner-webhook",
    provider: "GitHub",
    services: 1,
    openPullRequests: 1,
    health: "attention",
    lastAnalyzed: "2026-07-19T08:51:00Z",
  },
  {
    id: "repo-customer",
    name: "customer-service",
    provider: "GitHub",
    services: 1,
    openPullRequests: 1,
    health: "critical",
    lastAnalyzed: "2026-07-20T11:03:00Z",
  },
];
