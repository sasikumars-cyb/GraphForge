import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as repositoriesApi from "../lib/api/repositories";
import { RepositoriesPage } from "./RepositoriesPage";
import type {
  GetRepositoriesOverviewParams,
  RepositoryOverviewItem,
  RepositoryOverviewResponse,
} from "../lib/api/repositories";

const FAKE_USER = {
  id: "1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

// Deliberately more repositories than fit on a page — the whole point of
// this page's rewrite is that a large account renders a page's worth of
// rows, not all of them.
const TOTAL = 250;

function item(n: number): RepositoryOverviewItem {
  return {
    id: `repo-${n}`,
    name: `service-${n}`,
    full_name: `acme/service-${n}`,
    source: "github",
    created_at: "2026-01-01T00:00:00Z",
    health: "healthy",
    open_pull_requests: 0,
    indexing_status: "indexed",
    indexing_in_progress: false,
    last_indexed_at: "2026-01-01T00:00:00Z",
  };
}

const ALL = Array.from({ length: TOTAL }, (_, i) => item(i));

/** Stands in for the backend: applies the same search + pagination the
 * real endpoint does, so assertions here are about the page asking for
 * the right slice rather than about mock bookkeeping. */
function fakeOverview(params: GetRepositoriesOverviewParams = {}): RepositoryOverviewResponse {
  const page = params.page ?? 1;
  const pageSize = params.pageSize ?? 24;
  const needle = (params.q ?? "").toLowerCase();
  const matching = needle ? ALL.filter((r) => r.full_name.toLowerCase().includes(needle)) : ALL;
  const start = (page - 1) * pageSize;
  return {
    items: matching.slice(start, start + pageSize),
    stats: {
      repositories_monitored: TOTAL,
      organization_count: 1,
      open_pull_request_count: 0,
      awaiting_analysis_count: 0,
      high_risk_this_week_count: 0,
      avg_indexing_time_ms: 60_000,
    },
    page,
    page_size: pageSize,
    total: matching.length,
    has_more: start + pageSize < matching.length,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <RepositoriesPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("RepositoriesPage", () => {
  let overviewSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
    overviewSpy = vi
      .spyOn(repositoriesApi, "getRepositoriesOverview")
      .mockImplementation(async (_token, params) => fakeOverview(params));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders only one page of repositories, not the whole account", async () => {
    renderPage();

    expect(await screen.findByText("service-0")).toBeInTheDocument();
    expect(screen.getByText("service-23")).toBeInTheDocument();
    // The 25th repository exists but is on the next page — a card grid
    // that rendered all 250 is exactly the failure this replaces.
    expect(screen.queryByText("service-24")).not.toBeInTheDocument();

    // The account-wide count still reports every repository, so paging
    // the list doesn't understate what's being tracked.
    expect(screen.getByText("250")).toBeInTheDocument();
    expect(screen.getAllByText(/Showing 1–24 of 250 repositories/).length).toBeGreaterThan(0);
  });

  it("asks the server for the next page rather than slicing a full list", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("service-0");

    await user.click(screen.getAllByRole("button", { name: "Next page" })[0]);

    expect(await screen.findByText("service-24")).toBeInTheDocument();
    expect(screen.queryByText("service-0")).not.toBeInTheDocument();
    expect(overviewSpy).toHaveBeenCalledWith(
      "fake-token",
      expect.objectContaining({ page: 2 }),
      expect.anything(),
    );
  });

  it("searches server-side and resets to the first page", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("service-0");

    await user.click(screen.getAllByRole("button", { name: "Next page" })[0]);
    await screen.findByText("service-24");

    await user.type(screen.getByLabelText("Search repositories"), "service-117");

    await waitFor(() =>
      expect(overviewSpy).toHaveBeenCalledWith(
        "fake-token",
        // Back to page 1: staying on page 2 of a one-page result set
        // shows an empty list with matches sitting just out of reach.
        expect.objectContaining({ q: "service-117", page: 1 }),
        expect.anything(),
      ),
    );
    expect(await screen.findByText("service-117")).toBeInTheDocument();
  });

  it("keeps bulk-index selections when paging", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("service-0");

    await user.click(screen.getByText("Manage & bulk index"));
    const table = await screen.findByRole("table");
    await user.click(within(table).getByLabelText("Select acme/service-0"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Next page" })[0]);
    await screen.findByText("service-24");

    // Reaching a repository can now take several pages, so a selection
    // made on page 1 has to survive the trip.
    expect(screen.getByText("1 selected")).toBeInTheDocument();
  });
});
