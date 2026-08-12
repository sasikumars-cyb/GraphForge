import { apiFetch } from "./client";
import type { AskResponse } from "../../types/ask";

/** `POST /ask` — resolved deterministically (blast radius / dependency
 * search) when the question and an indexed repository match confidently
 * enough; otherwise `status: "route_to_investigation"`, meaning the
 * caller should fall back to the existing free-text investigation flow
 * (`createAgentRun` with `goal: "discover_context"`) — see the backend
 * router's own docstring for why this endpoint doesn't attempt that
 * itself. */
export function askGraphForge(
  token: string,
  question: string,
  signal?: AbortSignal,
): Promise<AskResponse> {
  return apiFetch<AskResponse>("/ask", {
    method: "POST",
    token,
    body: { question },
    signal,
  });
}
