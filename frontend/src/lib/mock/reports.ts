import type { Report } from "../../types/domain";

/** Sample data only — no backend is connected yet. */
export const mockReports: Report[] = [
  {
    id: "rpt-1042",
    name: "Change evidence — PR #1042",
    repository: "payment-service",
    risk: "critical",
    status: "generating",
    generatedAt: "2026-07-21T09:13:00Z",
  },
  {
    id: "rpt-1035",
    name: "Change evidence — PR #1035",
    repository: "customer-service",
    risk: "high",
    status: "ready",
    generatedAt: "2026-07-20T11:10:00Z",
  },
  {
    id: "rpt-1028",
    name: "Change evidence — PR #1028",
    repository: "partner-webhook",
    risk: "high",
    status: "ready",
    generatedAt: "2026-07-19T09:02:00Z",
  },
  {
    id: "rpt-1031",
    name: "Change evidence — PR #1031",
    repository: "order-service",
    risk: "low",
    status: "ready",
    generatedAt: "2026-07-19T14:30:00Z",
  },
  {
    id: "rpt-1019",
    name: "Change evidence — PR #1019",
    repository: "notification-service",
    risk: "medium",
    status: "failed",
    generatedAt: "2026-07-17T10:20:00Z",
  },
];
