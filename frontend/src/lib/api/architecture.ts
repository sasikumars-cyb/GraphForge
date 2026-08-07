import { apiFetch } from "./client";
import type { ArchitectureSummary } from "../../types/architecture";

/** ADR 0023 — the org-scale landing query: every tracked repository's
 * indexing status, real node counts, and domain grouping, in one call.
 * Replaces the old per-repository indexing-job fan-out. */
export function getArchitectureSummary(
  token: string,
  signal?: AbortSignal,
): Promise<ArchitectureSummary> {
  return apiFetch<ArchitectureSummary>("/architecture/summary", { token, signal });
}
