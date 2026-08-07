import { apiFetch } from "./client";
import type { BlastRadius } from "../../types/impact";

export interface GetBlastRadiusParams {
  /** Omit to seed from the repository's own node. */
  nodeId?: string;
  maxHops?: number;
}

/** The fast, deterministic blast-radius endpoint Impact Check's radial
 * graph reads directly — bypasses the LLM-narrated impact_analysis agent
 * entirely (see backend/app/api/v1/routers/impact.py's own docstring for
 * why). */
export function getBlastRadius(
  token: string,
  repositoryId: string,
  params: GetBlastRadiusParams = {},
  signal?: AbortSignal,
): Promise<BlastRadius> {
  const searchParams = new URLSearchParams();
  if (params.nodeId !== undefined) searchParams.set("node_id", params.nodeId);
  if (params.maxHops !== undefined) searchParams.set("max_hops", String(params.maxHops));
  const qs = searchParams.toString();
  return apiFetch<BlastRadius>(`/repositories/${repositoryId}/impact${qs ? `?${qs}` : ""}`, {
    token,
    signal,
  });
}
