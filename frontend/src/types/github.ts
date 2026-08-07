/**
 * Types for the real (non-mock) GitHub integration API — mirrors
 * backend/app/schemas/github.py.
 */

export interface GitHubConnectionStatus {
  connected: boolean;
  github_username: string | null;
  connected_at: string | null;
  /** "oauth" | "pat" | null (not connected). */
  auth_method: "oauth" | "pat" | null;
  /** Set only just after a PAT connect whose token is missing the `repo`
   * scope — advisory, never blocks the connection. */
  scope_warning: string | null;
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

/** One repo already tracked (persisted) in GraphForge. */
export interface TrackedRepository {
  id: string;
  github_repo_id: string;
  /** "github" | "local" — see backend Repository.source. */
  source: "github" | "local";
  owner: string;
  name: string;
  full_name: string;
  private: boolean;
  default_branch: string;
  html_url: string;
  // ADR 0023 — manual repository grouping; null/absent means ungrouped.
  // Optional (not just nullable) rather than required so the many
  // existing test fixtures across this codebase that construct a
  // TrackedRepository without it don't all need updating for a field
  // most of them have no reason to care about.
  domain?: string | null;
  created_at: string;
}

/** Body for POST /repositories/local — the branch indexed is always
 * auto-detected server-side from the folder's current git checkout. */
export interface LocalRepositoryCreateRequest {
  name: string;
  /** Relative to the server's configured LOCAL_REPOS_ROOT. */
  path: string;
}
