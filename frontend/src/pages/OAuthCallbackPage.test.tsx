import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";

function renderAt(path: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
          <Route path="/" element={<div>Dashboard placeholder</div>} />
          <Route path="/login" element={<div>Login placeholder</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("OAuthCallbackPage", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("adopts a ?token= and navigates to /", async () => {
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue({
      id: "1",
      email: "octocat@example.com",
      full_name: "Octocat",
      auth_provider: "github",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    });

    renderAt("/oauth/callback?token=fake-github-token");

    expect(await screen.findByText("Dashboard placeholder")).toBeInTheDocument();
    expect(localStorage.getItem("graphforge.token")).toBe("fake-github-token");
  });

  it("shows a mapped message for a known ?error= code", async () => {
    renderAt("/oauth/callback?error=github_account_is_local");

    expect(await screen.findByText(/log in with your password instead/i)).toBeInTheDocument();
  });

  it("falls back to a generic message for an unrecognized ?error= code", async () => {
    renderAt("/oauth/callback?error=some_future_error_code");

    expect(
      await screen.findByText(/something went wrong signing in with github/i),
    ).toBeInTheDocument();
  });

  it("redirects to /login when neither token nor error is present", async () => {
    renderAt("/oauth/callback");

    expect(await screen.findByText("Login placeholder")).toBeInTheDocument();
  });
});
