/**
 * API functions for the Engineering Task endpoints (Phase 7 / 7.1 / 7.2).
 * Follows the existing apiFetch convention from client.ts.
 *
 * Phase 7.2 adds `listEngineeringTasks` (GET) and `createEngineeringTask`
 * (POST, the existing, unmodified endpoint) — the first write call in this
 * module. Still deliberately narrow: no update/delete wrapper exists,
 * matching Engineering State's own append-only, immutable-event design —
 * there is nothing to update or delete.
 */

import { apiFetch } from "./client";
import type {
  CreateEngineeringTaskInput,
  EngineeringTask,
  EngineeringTaskSummary,
} from "../../types/engineeringTask";

export function getEngineeringTask(
  token: string,
  taskId: string,
  signal?: AbortSignal,
): Promise<EngineeringTask> {
  return apiFetch<EngineeringTask>(`/engineering-tasks/${taskId}`, {
    token,
    signal,
  });
}

export function listEngineeringTasks(
  token: string,
  signal?: AbortSignal,
): Promise<EngineeringTaskSummary[]> {
  return apiFetch<EngineeringTaskSummary[]>("/engineering-tasks", {
    token,
    signal,
  });
}

export function createEngineeringTask(
  token: string,
  input: CreateEngineeringTaskInput,
): Promise<EngineeringTask> {
  return apiFetch<EngineeringTask>("/engineering-tasks", {
    method: "POST",
    token,
    body: input,
  });
}
