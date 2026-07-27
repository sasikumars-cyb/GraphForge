import { apiFetch } from "./client";
import type { AgentManifest } from "../../types/agents";

export function listAgentManifests(token: string): Promise<AgentManifest[]> {
  return apiFetch<AgentManifest[]>("/agent-runs/agents/manifests", { token });
}

export function disableAgent(token: string, agentId: string): Promise<void> {
  return apiFetch<void>(`/agent-runs/agents/${encodeURIComponent(agentId)}/disable`, {
    method: "POST",
    token,
    body: {},
  });
}

export function enableAgent(token: string, agentId: string): Promise<void> {
  return apiFetch<void>(`/agent-runs/agents/${encodeURIComponent(agentId)}/enable`, {
    method: "POST",
    token,
    body: {},
  });
}
