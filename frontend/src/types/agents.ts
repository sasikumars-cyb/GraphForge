export interface AgentManifest {
  agent_id: string;
  purpose: string;
  goals: string[];
  cost_class: string;
  enabled: boolean;
}
