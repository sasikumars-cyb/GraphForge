import { apiFetch } from "./client";
import type { ParityReport } from "../../types/parity";

/** GET /repositories/{id}/parity — runs a fresh, read-only comparison
 * between the legacy Neo4j graph and the Engineering Memory projection.
 * No caching on either side: every call recomputes from live state. */
export function getRepositoryParity(token: string, repositoryId: string): Promise<ParityReport> {
  return apiFetch<ParityReport>(`/repositories/${repositoryId}/parity`, { token });
}
