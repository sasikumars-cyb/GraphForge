import { render, screen, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "./AuthContext";
import { useAuth } from "./auth-context";
import { UNAUTHORIZED_EVENT } from "../lib/api/client";
import * as authApi from "../lib/api/auth";

const TOKEN_KEY = "graphforge.token";

function Probe() {
  const { user, token } = useAuth();
  return (
    <div>
      <span data-testid="token">{token ?? "none"}</span>
      <span data-testid="user">{user ? user.email : "none"}</span>
    </div>
  );
}

describe("AuthProvider — session-expiry handling", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("logs out when UNAUTHORIZED_EVENT fires, clearing token/user and localStorage", async () => {
    localStorage.setItem(TOKEN_KEY, "a-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue({
      id: "1",
      email: "ada@example.com",
      full_name: "Ada Lovelace",
      auth_provider: "local",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("ada@example.com"));

    // Regression test: previously nothing ever re-checked session validity
    // after the initial mount — a token dying mid-session (expiry, or the
    // account being deactivated) left the user stuck on broken pages with
    // no path back to /login short of a manual browser refresh.
    act(() => {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    });

    await waitFor(() => expect(screen.getByTestId("token")).toHaveTextContent("none"));
    expect(screen.getByTestId("user")).toHaveTextContent("none");
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
