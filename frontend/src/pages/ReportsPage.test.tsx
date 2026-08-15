import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("ReportsPage — stored view models older than the current document format", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders a pre-review-outcome view model through the legacy HTML fallback instead of crashing", async () => {
    // `workflow_reports.view_model` is a JSON column that is never
    // migrated, so a report generated before the Engineering Review
    // outcome sections existed still deserializes into `ReportViewModel`
    // while missing `review_outcome`/`findings`.
    const stale = {
      header: { question: "old question", workflow_title: "w", repository: null, readiness: "unknown", generated_at: "" },
      confidence: { availability: { status: "unavailable", reason: "x" }, current: null, points: [], summary_sentence: "" },
      timeline: { availability: { status: "unavailable", reason: "x" }, steps: [], truncated_count: 0 },
      knowledge: { availability: { status: "unavailable", reason: "x" }, known: [], known_truncated_count: 0, unknown: [], unknown_truncated_count: 0 },
      hypotheses: { availability: { status: "unavailable", reason: "x" }, synthesis_state: "not_run", items: [], truncated_count: 0 },
      contradictions: { availability: { status: "unavailable", reason: "x" }, synthesis_state: "not_run", items: [] },
      evidence: { availability: { status: "unavailable", reason: "x" }, categories: [], total: 0 },
      next_actions: { availability: { status: "available", reason: null }, questions: [] },
      executive_summary: "an older report",
    } as unknown as reportsApi.ReportViewModel;

    expect(reportsApi.isCurrentViewModel(stale)).toBe(false);
    expect(reportsApi.isCurrentViewModel(null)).toBe(false);

    vi.spyOn(reportsApi, "listReports").mockResolvedValue([REPORT]);
    vi.spyOn(reportsApi, "getReport").mockResolvedValue({
      ...REPORT,
      html_content: "<html><body>legacy rendering</body></html>",
      view_model: stale,
    });

    render(
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>,
    );
    // The page renders the list without throwing; the detail pane falls
    // back to the stored HTML for this report.
    expect(await screen.findByText(REQUEST)).toBeInTheDocument();
  });
});

describe("ReportsPage — deleting a report", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function renderWithReport(report: ReportSummary = REPORT) {
    const list = vi.spyOn(reportsApi, "listReports").mockResolvedValue([report]);
    render(
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>,
    );
    await screen.findByText(report.request || report.title);
    return list;
  }

  it("asks for confirmation before deleting, and deletes nothing until confirmed", async () => {
    await renderWithReport();
    const del = vi.spyOn(reportsApi, "deleteReport").mockResolvedValue(undefined);

    await userEvent.click(screen.getByRole("button", { name: /delete report:/i }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();
  });

  it("says the investigation survives — this deletes a document, not a workflow", async () => {
    await renderWithReport();
    await userEvent.click(screen.getByRole("button", { name: /delete report:/i }));

    expect(screen.getByText(/removes the generated report only/i)).toBeInTheDocument();
    expect(screen.getByText(/investigation behind it/i)).toBeInTheDocument();
    expect(screen.getByText(/is kept/i)).toBeInTheDocument();
  });

  it("deletes the report and refreshes the list once confirmed", async () => {
    const list = await renderWithReport();
    const del = vi.spyOn(reportsApi, "deleteReport").mockResolvedValue(undefined);
    list.mockResolvedValue([]);

    await userEvent.click(screen.getByRole("button", { name: /delete report:/i }));
    await userEvent.click(screen.getByRole("button", { name: "Delete report" }));

    await waitFor(() => expect(del).toHaveBeenCalledWith("fake-token", REPORT.id));
    // The row is gone because the list was re-fetched, not because the
    // component optimistically hid it — the server is the source of truth.
    await waitFor(() => expect(screen.queryByText(REQUEST)).not.toBeInTheDocument());
    expect(screen.getByText(/No reports generated yet/i)).toBeInTheDocument();
  });

  it("keeps the row and shows why when the delete fails", async () => {
    await renderWithReport();
    vi.spyOn(reportsApi, "deleteReport").mockRejectedValue(new Error("Report is locked."));

    await userEvent.click(screen.getByRole("button", { name: /delete report:/i }));
    await userEvent.click(screen.getByRole("button", { name: "Delete report" }));

    expect(await screen.findByText("Report is locked.")).toBeInTheDocument();
    expect(screen.getByText(REQUEST)).toBeInTheDocument();
  });

  it("offers delete for a failed report, which has no View report button to sit beside", async () => {
    await renderWithReport({
      ...REPORT,
      status: "failed",
      error_message: "Generation failed.",
    });

    expect(screen.getByRole("button", { name: /delete report:/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /view report/i })).not.toBeInTheDocument();
  });
});
