import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as reportsApi from "../lib/api/reports";
import { ReportsPage } from "./ReportsPage";
import type { ReportSummary } from "../lib/api/reports";

const FAKE_USER = {
  id: "1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

const REQUEST =
  "We're seeing intermittent 504s on checkout during peak hours. " +
  "Work out whether the synchronous call into payments is the cause.";

const REPORT: ReportSummary = {
  id: "report-1",
  workflow_id: "workflow-1",
  workflow_title: "Checkout 504 investigation",
  title: "Checkout 504 investigation",
  request: REQUEST,
  status: "completed",
  error_message: null,
  created_at: "2026-01-01T00:00:00Z",
  completed_at: "2026-01-01T00:05:00Z",
};

describe("ReportsPage", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("identifies a report by the request the user made", async () => {
    vi.spyOn(reportsApi, "listReports").mockResolvedValue([REPORT]);

    render(
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>,
    );

    // The request itself, not just the AI-generated workflow label — a
    // report answers a question somebody asked, so that question is what
    // tells two reports apart in this list.
    expect(await screen.findByText(REQUEST)).toBeInTheDocument();
  });

  it("falls back to the report title when the request is unavailable", async () => {
    // `request` is empty only when the report's workflow row is gone; the
    // row must still be identifiable rather than rendering as blank.
    vi.spyOn(reportsApi, "listReports").mockResolvedValue([{ ...REPORT, request: "" }]);

    render(
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>,
    );

    expect(await screen.findAllByText("Checkout 504 investigation")).not.toHaveLength(0);
  });
});
