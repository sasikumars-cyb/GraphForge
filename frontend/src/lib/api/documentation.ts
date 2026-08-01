import { apiFetch } from "./client";

export interface CreateDocumentationPRResponse {
  pull_request_url: string;
  branch_name: string;
  files_changed: number;
}

/** POST /documentation/runs/{run_id}/create-pr — see backend
 * app.api.v1.routers.documentation for what this actually does (opens one
 * branch/commit/PR applying every proposed_update + proposed_new_document
 * from a completed review_documentation run). */
export function createDocumentationPR(
  token: string,
  runId: string,
): Promise<CreateDocumentationPRResponse> {
  return apiFetch<CreateDocumentationPRResponse>(
    `/documentation/runs/${encodeURIComponent(runId)}/create-pr`,
    { method: "POST", token },
  );
}
