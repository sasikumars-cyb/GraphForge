import { apiFetch } from "./client";
import type {
  AvailableRepository,
  GitHubConnectionStatus,
  TrackedRepository,
} from "../../types/github";

export function getConnectionStatus(
  token: string,
  signal?: AbortSignal,
): Promise<GitHubConnectionStatus> {
  return apiFetch<GitHubConnectionStatus>("/github/connection", { token, signal });
}

export function getConnectAuthorizationUrl(token: string): Promise<{ authorization_url: string }> {
  return apiFetch<{ authorization_url: string }>("/github/connect", { token });
}

export function disconnectGitHub(token: string): Promise<undefined> {
  return apiFetch<undefined>("/github/connection", { method: "DELETE", token });
}

export function listAvailableRepositories(token: string): Promise<AvailableRepository[]> {
  return apiFetch<AvailableRepository[]>("/github/repositories", { token });
}

export function listTrackedRepositories(token: string): Promise<TrackedRepository[]> {
  return apiFetch<TrackedRepository[]>("/repositories", { token });
}

export function saveSelectedRepositories(
  token: string,
  repositories: AvailableRepository[],
): Promise<TrackedRepository[]> {
  const body = {
    repositories: repositories.map((repo) => ({
      provider_repo_id: repo.provider_repo_id,
      owner: repo.owner,
      name: repo.name,
      full_name: repo.full_name,
      private: repo.private,
      default_branch: repo.default_branch,
      html_url: repo.html_url,
    })),
  };
  return apiFetch<TrackedRepository[]>("/repositories", { method: "POST", token, body });
}
