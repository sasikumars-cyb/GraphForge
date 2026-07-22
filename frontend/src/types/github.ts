/**
 * Types for the real (non-mock) GitHub integration API — mirrors
 * backend/app/schemas/github.py.
 */

export interface GitHubConnectionStatus {
  connected: boolean;
  github_username: string | null;
  connected_at: string | null;
}

/** One repo from the user's live GitHub list. */
export interface AvailableRepository {
  provider_repo_id: string;
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  is_selected: boolean;
}

/** One repo already tracked (persisted) in ChangeGuard. */
export interface TrackedRepository {
  id: string;
  github_repo_id: string;
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  created_at: string;
}
