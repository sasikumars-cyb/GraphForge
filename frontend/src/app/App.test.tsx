import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "./router";
import { AuthProvider } from "./AuthContext";
import * as authApi from "../lib/api/auth";
import type { User } from "../types/auth";

const FAKE_USER: User = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  created_at: "2026-01-01T00:00:00Z",
};

function renderApp(initialPath = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  return render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  );
}

describe("App navigation (authenticated)", () => {
  beforeEach(() => {
    localStorage.setItem("changeguard.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders the sidebar with a link to every page", async () => {
    renderApp();
    const nav = await screen.findByRole("navigation");

    for (const label of [
      "Dashboard",
      "Pull Requests",
      "Repositories",
      "Architecture",
      "Reports",
      "Settings",
    ]) {
      expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("defaults to the Dashboard page", async () => {
    renderApp();
    // Both the Topbar (h1) and the page itself (h2) show the label, so
    // assert on the page-level heading specifically.
    expect(await screen.findByRole("heading", { level: 2, name: "Dashboard" })).toBeInTheDocument();
  });

  it("navigates to Pull Requests when its sidebar link is clicked", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("link", { name: "Pull Requests" }));

    expect(
      await screen.findByRole("heading", { level: 2, name: "Pull Requests" }),
    ).toBeInTheDocument();
  });

  it.each([
    ["/repositories", "Repositories"],
    ["/architecture", "Architecture"],
    ["/reports", "Reports"],
    ["/settings", "Settings"],
  ])("renders the %s page at %s", async (path, heading) => {
    renderApp(path);
    expect(await screen.findByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
  });

  it("shows the logged-in user's name and logs out via the sidebar", async () => {
    const user = userEvent.setup();
    renderApp();

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();

    await user.click(screen.getByTitle("Log out"));

    expect(
      await screen.findByRole("heading", { name: "Sign in to ChangeGuard" }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("changeguard.token")).toBeNull();
  });
});

describe("App navigation (unauthenticated)", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("redirects a page route to /login when there is no token", async () => {
    renderApp("/pull-requests");

    expect(
      await screen.findByRole("heading", { name: "Sign in to ChangeGuard" }),
    ).toBeInTheDocument();
  });

  it("drops an invalid token and redirects to /login", async () => {
    localStorage.setItem("changeguard.token", "invalid-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockRejectedValue(new Error("401"));

    renderApp("/");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Sign in to ChangeGuard" })).toBeInTheDocument(),
    );
    expect(localStorage.getItem("changeguard.token")).toBeNull();
  });
});
