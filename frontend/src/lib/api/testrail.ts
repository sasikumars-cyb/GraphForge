import { apiFetch } from "./client";

export interface TestRailProject {
  id: number;
  name: string;
  last_sync_status: string | null;
  last_synced_at: string | null;
  case_count: number | null;
}

export interface TestRailSyncJob {
  id: string;
  testrail_project_id: number;
  project_name: string;
  status: string;
  error_message: string | null;
  result_summary: Record<string, number> | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export function listTestRailProjects(token: string): Promise<TestRailProject[]> {
  return apiFetch<TestRailProject[]>("/testrail/projects", { token });
}

export function syncTestRailProject(
  token: string,
  projectId: number,
  projectName: string,
): Promise<TestRailSyncJob> {
  return apiFetch<TestRailSyncJob>(`/testrail/projects/${projectId}/sync`, {
    method: "POST",
    token,
    body: { project_name: projectName },
  });
}

export function getTestRailSyncStatus(token: string, projectId: number): Promise<TestRailSyncJob> {
  return apiFetch<TestRailSyncJob>(`/testrail/projects/${projectId}/sync`, { token });
}
