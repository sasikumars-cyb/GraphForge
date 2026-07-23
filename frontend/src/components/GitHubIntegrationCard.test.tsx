import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitHubIntegrationCard } from "./GitHubIntegrationCard";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
import type { AvailableRepository, GitHubConnectionStatus } from "../types/github";

const FAKE_USER = {
  id: "1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  created_at: "2026-01-01T00:00:00Z",
};

const NOT_CONNECTED: GitHubConnectionStatus = {
  connected: false,
  github_username: null,
  connected_at: null,
};

const CONNECTED: GitHubConnectionStatus = {
  connected: true,
  github_username: "ada",
  connected_at: "2026-07-01T00:00:00Z",
};

const REPOS: AvailableRepository[] = [
  {
    provider_repo_id: "1001",
    owner: "ada",
    name: "engine",
    full_name: "ada/engine",
    private: false,
    default_branch: "main",
    html_url: "https://github.com/ada/engine",
    is_selected: true,
  },
  {
    provider_repo_id: "1002",
    owner: "ada",
    name: "notes",
    full_name: "ada/notes",
    private: true,
    default_branch: "main",
    html_url: "https://github.com/ada/notes",
    is_selected: false,
  },
];

function renderCard(initialEntries: string[] = ["/settings"]) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <GitHubIntegrationCard />
      </MemoryRouter>
    </AuthProvider>,
  );
}

describe("GitHubIntegrationCard", () => {
  beforeEach(() => {
    localStorage.setItem("graphforge.token", "fake-token");
    vi.spyOn(authApi, "fetchCurrentUser").mockResolvedValue(FAKE_USER);
    Object.defineProperty(window, "location", {
      value: { href: "" },
      writable: true,
    });
  });

  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("shows Not connected and a Connect button by default", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    renderCard();

    expect(await screen.findByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("redirects to the authorization URL when Connect is clicked", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    vi.spyOn(githubApi, "getConnectAuthorizationUrl").mockResolvedValue({
      authorization_url: "https://github.com/login/oauth/authorize?client_id=abc&state=xyz",
    });
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(window.location.href).toBe(
        "https://github.com/login/oauth/authorize?client_id=abc&state=xyz",
      ),
    );
  });

  it("shows the connected username and the repository checklist", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    renderCard();

    expect(await screen.findByText("Connected as @ada")).toBeInTheDocument();
    const engineCheckbox = await screen.findByRole("checkbox", { name: /ada\/engine/ });
    const notesCheckbox = screen.getByRole("checkbox", { name: /ada\/notes/ });
    expect(engineCheckbox).toBeChecked();
    expect(notesCheckbox).not.toBeChecked();
  });

  it("saves the updated selection when a repo is toggled and Save is clicked", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    const saveSpy = vi.spyOn(githubApi, "saveSelectedRepositories").mockResolvedValue([]);
    const user = userEvent.setup();
    renderCard();

    const notesCheckbox = await screen.findByRole("checkbox", { name: /ada\/notes/ });
    await user.click(notesCheckbox);
    await user.click(screen.getByRole("button", { name: "Save selection" }));

    await waitFor(() => expect(saveSpy).toHaveBeenCalledTimes(1));
    const [, savedRepos] = saveSpy.mock.calls[0];
    expect(savedRepos.map((repo) => repo.full_name).sort()).toEqual(["ada/engine", "ada/notes"]);
  });

  it("disconnects and returns to the Not connected state", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    const disconnectSpy = vi.spyOn(githubApi, "disconnectGitHub").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderCard();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));

    expect(disconnectSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Not connected")).toBeInTheDocument();
  });

  it("shows a success notice after the OAuth callback redirects with ?github=connected", async () => {
    const statusSpy = vi
      .spyOn(githubApi, "getConnectionStatus")
      .mockResolvedValueOnce(NOT_CONNECTED)
      .mockResolvedValueOnce(CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    renderCard(["/settings?github=connected"]);

    expect(await screen.findByText("GitHub connected.")).toBeInTheDocument();
    await waitFor(() => expect(statusSpy).toHaveBeenCalledTimes(2));
  });

  it("shows an error notice after the OAuth callback redirects with ?github=error", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    renderCard(["/settings?github=error"]);

    expect(
      await screen.findByText("Connecting to GitHub failed. Please try again."),
    ).toBeInTheDocument();
  });
});
