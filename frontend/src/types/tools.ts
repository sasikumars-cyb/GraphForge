export type ToolCategory =
  | "graph"
  | "code_intelligence"
  | "project_management"
  | "documentation"
  | "communication"
  | "monitoring"
  | "filesystem"
  | "custom";

export type ToolHealth =
  | "healthy"
  | "unconfigured"
  | "offline"
  | "auth_failed"
  | "permission_denied"
  | "rate_limited"
  | "unavailable";

export interface Tool {
  tool_id: string;
  display_name: string;
  description: string;
  category: ToolCategory;
  capabilities: string[];
  requires_auth: boolean;
  auth_fields: string[];
  default_enabled: boolean;
  enabled: boolean;
  health: ToolHealth;
  icon: string;
  notes: string;
}

export interface HealthCheckResult {
  tool_id: string;
  health: ToolHealth;
}

export interface ConfigureToolRequest {
  enabled: boolean;
  config: Record<string, string>;
}
