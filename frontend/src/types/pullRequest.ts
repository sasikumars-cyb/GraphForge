/**
 * Types for the real pull request API — mirrors backend/app/schemas/github.py's
 * `PullRequestResponse`.
 */

export interface PullRequest {
  id: string;
  number: number;
  title: string;
  state: string;
  is_draft: boolean;
  author_login: string;
  html_url: string;
  head_ref: string;
  base_ref: string;
  github_created_at: string;
  github_updated_at: string;
}
