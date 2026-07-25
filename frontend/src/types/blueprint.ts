/**
 * Visual Engineering Blueprint types — mirrors backend/app/agents/blueprint/models.py.
 *
 * Stored inside AgentStep.result.blueprint (already present in step.result as
 * Record<string, unknown>). Cast with `step.result.blueprint as BlueprintArtifact`.
 */

export type DiagramType =
  | "flow"
  | "architecture"
  | "sequence"
  | "dependency"
  | "timeline"
  | "er"
  | "risk_heatmap";

export type NodeType =
  | "default"
  | "input"
  | "output"
  | "component"
  | "topic"
  | "risk"
  | "phase"
  | "entity";

export interface DiagramNode {
  id: string;
  label: string;
  type?: NodeType;
  properties?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface DiagramEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  type?: string;
  metadata?: Record<string, unknown>;
}

export interface DiagramLayout {
  direction?: "LR" | "TB" | "RL" | "BT";
  algorithm?: "dagre" | "manual" | "grid";
}

export interface Diagram {
  id: string;
  title: string;
  description?: string;
  type: DiagramType;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  layout?: DiagramLayout;
  metadata?: Record<string, unknown>;
  evidence?: string[];
  confidence?: number | null;
}

export interface BlueprintArtifact {
  agent_id: string;
  stage: string;
  diagrams: Diagram[];
  version: string;
}
