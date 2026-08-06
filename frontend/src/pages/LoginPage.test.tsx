import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./LoginPage";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import { ApiError } from "../lib/api/client";

function renderLoginPage() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>Dashboard placeholder</div>} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("LoginPage", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders email, password, and a GitHub sign-in link", async () => {
    renderLoginPage();

    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    // KAN-34 - a real top-level navigation to the backend's OAuth login
    // route, not a same-origin SPA link - see LoginPage's own comment.
    const githubLink = screen.getByRole("link", { name: /continue with github/i });
    expect(githubLink).toHaveAttribute("href", expect.stringContaining("/auth/github/login"));
  });

  it("shows the backend's error message when login fails", async () => {
    vi.spyOn(authApi, "login").mockRejectedValue(
      new ApiError(401, "unauthorized", "Incorrect email or password."),
    );
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
  });

  it("logs in and navigates to / on success", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue({
      access_token: "fake-token",
      token_type: "bearer",
    });
    // AuthProvider fetches the current user as soon as a token appears;
    // stub it too so that call doesn't hit the real network in the background.
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue({
      id: "1",
      email: "ada@example.com",
      full_name: "Ada Lovelace",
      auth_provider: "local",
      role: "user",
      created_at: "2026-01-01T00:00:00Z",
    });
    const user = userEvent.setup();
    renderLoginPage();

    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Dashboard placeholder")).toBeInTheDocument();
    expect(localStorage.getItem("graphforge.token")).toBe("fake-token");
  });
});
