export interface CalibrationBucketStat {
  bucket: string;
  total: number;
  approved: number;
  approval_rate: number;
}

export interface AgentCalibration {
  agent_id: string;
  total_decisions: number;
  approval_rate: number;
  avg_confidence: number;
  buckets: CalibrationBucketStat[];
}

export interface CalibrationSummary {
  agents: AgentCalibration[];
}
