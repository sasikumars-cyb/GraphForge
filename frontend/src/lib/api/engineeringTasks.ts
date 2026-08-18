/**
 * API functions for the Engineering Task endpoints (Phase 7 / 7.1).
 * Follows the existing apiFetch convention from client.ts.
 *
 * Deliberately read-only: only `getEngineeringTask` (GET) is exported.
 * Creation stays API-only for this increment — no `createEngineeringTask`
 * wrapper is added here, so this module cannot be used to mutate
 * Engineering State from the UI even by accident.
 */

import { apiFetch } from "./client";
import type { EngineeringTask } from "../../types/engineeringTask";

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
