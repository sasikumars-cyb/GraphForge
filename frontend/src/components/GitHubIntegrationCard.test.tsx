import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitHubIntegrationCard } from "./GitHubIntegrationCard";
import { AuthProvider } from "../app/AuthContext";
import * as authApi from "../lib/api/auth";
import * as githubApi from "../lib/api/github";
import * as repositoriesApi from "../lib/api/repositories";
import type { AvailableRepository, GitHubConnectionStatus } from "../types/github";

const FAKE_USER = {
  id: "1",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  auth_provider: "local",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
};

const NOT_CONNECTED: GitHubConnectionStatus = {
  connected: false,
  github_username: null,
  connected_at: null,
  auth_method: null,
  scope_warning: null,
};

const CONNECTED: GitHubConnectionStatus = {
  connected: true,
  github_username: "ada",
  connected_at: "2026-07-01T00:00:00Z",
  auth_method: "oauth",
  scope_warning: null,
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

async function openAddConnection() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Add Connection" }));
  return user;
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

  it("shows Not connected and an Add Connection button by default", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    renderCard();

    expect(await screen.findByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Connection" })).toBeInTheDocument();
  });

  it("redirects to the authorization URL when Connect via OAuth is clicked", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    vi.spyOn(githubApi, "getConnectAuthorizationUrl").mockResolvedValue({
      authorization_url: "https://github.com/login/oauth/authorize?client_id=abc&state=xyz",
    });
    renderCard();
    const user = await openAddConnection();

    await user.click(screen.getByRole("button", { name: "Connect via OAuth" }));

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

  it("connects with a pasted personal access token", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    const connectSpy = vi
      .spyOn(githubApi, "connectWithPersonalAccessToken")
      .mockResolvedValue({ ...CONNECTED, auth_method: "pat" });
    renderCard();
    const user = await openAddConnection();

    await user.type(screen.getByLabelText("Personal access token"), "ghp_faketoken");
    await user.click(screen.getByRole("button", { name: "Connect with token" }));

    expect(connectSpy).toHaveBeenCalledWith("fake-token", "ghp_faketoken");
    expect(
      await screen.findByText("Connected as @ada via personal access token"),
    ).toBeInTheDocument();
  });

  it("surfaces a scope warning after a PAT connect that's missing the repo scope", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    vi.spyOn(githubApi, "listAvailableRepositories").mockResolvedValue(REPOS);
    vi.spyOn(githubApi, "connectWithPersonalAccessToken").mockResolvedValue({
      ...CONNECTED,
      auth_method: "pat",
      scope_warning: "This token doesn't have the 'repo' scope.",
    });
    renderCard();
    const user = await openAddConnection();

    await user.type(screen.getByLabelText("Personal access token"), "ghp_faketoken");
    await user.click(screen.getByRole("button", { name: "Connect with token" }));

    expect(
      await screen.findByText(/This token doesn't have the 'repo' scope\./),
    ).toBeInTheDocument();
  });

  it("adds a local repository", async () => {
    vi.spyOn(githubApi, "getConnectionStatus").mockResolvedValue(NOT_CONNECTED);
    const createSpy = vi.spyOn(repositoriesApi, "createLocalRepository").mockResolvedValue({
      id: "repo-1",
      github_repo_id: "local:order-service",
      source: "local",
      owner: "local",
      name: "order-service",
      full_name: "local/order-service",
      private: false,
      default_branch: "main",
      html_url: "/local-repos/order-service",
      created_at: "2026-07-30T00:00:00Z",
    });
    renderCard();
    const user = await openAddConnection();

    await user.type(screen.getByLabelText("Local repository name"), "order-service");
    await user.type(screen.getByLabelText("Local repository path"), "order-service");
    await user.click(screen.getByRole("button", { name: "Add local repository" }));

    expect(createSpy).toHaveBeenCalledWith("fake-token", {
      name: "order-service",
      path: "order-service",
    });
    expect(await screen.findByText("Tracking 'order-service' (branch: main).")).toBeInTheDocument();
  });
});
