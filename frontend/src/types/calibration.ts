export interface CalibrationBucketStat {
  bucket: string;
  total: number;
  approved: number;
  approval_rate: number;
}

// KAN-23 — per prompt_version breakdown, so a version whose approval rate
// has drifted from its agent's overall rate can be spotted directly
// instead of being averaged away in the agent-level numbers above it.
export interface PromptVersionStat {
  prompt_version: string;
  total: number;
  approved: number;
  approval_rate: number;
  avg_confidence: number;
  flagged_miscalibrated: boolean;
}

export interface AgentCalibration {
  agent_id: string;
  total_decisions: number;
  approval_rate: number;
  avg_confidence: number;
  buckets: CalibrationBucketStat[];
  by_prompt_version: PromptVersionStat[];
}

export interface CalibrationSummary {
  agents: AgentCalibration[];
}
