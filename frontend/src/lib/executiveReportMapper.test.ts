import { describe, expect, it } from "vitest";
import {
  formatDuration,
  formatTokens,
  formatCost,
  formatConfidence,
  statusToTone,
  stageStatusToTone,
  mapSummary,
  mapTimeline,
  mapMetrics,
  mapCharts,
  mapRepositoryImpact,
  mapReviewResults,
  mapRecommendations,
} from "./executiveReportMapper";
import type { ExecutiveReportData } from "../types/executiveReport";

// ---------------------------------------------------------------------------
// Formatting utilities
// ---------------------------------------------------------------------------

describe("formatDuration", () => {
  it("returns dash for null", () => {
    expect(formatDuration(null)).toBe("\u2014");
  });

  it("returns dash for 0", () => {
    expect(formatDuration(0)).toBe("\u2014");
  });

  it("formats milliseconds as seconds", () => {
    expect(formatDuration(5000)).toBe("5.0s");
  });

  it("formats as minutes when >= 60s", () => {
    expect(formatDuration(120000)).toBe("2.0m");
  });

  it("formats as hours when >= 60m", () => {
    expect(formatDuration(7200000)).toBe("2.0h");
  });
});

describe("formatTokens", () => {
  it("returns raw number for < 1000", () => {
    expect(formatTokens(500)).toBe("500");
  });

  it("formats thousands with K", () => {
    expect(formatTokens(1500)).toBe("1.5K");
  });

  it("formats millions with M", () => {
    expect(formatTokens(2500000)).toBe("2.5M");
  });
});

describe("formatCost", () => {
  it("formats zero", () => {
    expect(formatCost(0)).toBe("$0.00");
  });

  it("formats small amounts with 4 decimals", () => {
    expect(formatCost(0.0012)).toBe("$0.0012");
  });

  it("formats larger amounts with 2 decimals", () => {
    expect(formatCost(1.5)).toBe("$1.50");
  });
});

describe("formatConfidence", () => {
  it("returns dash for null", () => {
    expect(formatConfidence(null)).toBe("\u2014");
  });

  it("formats as percentage", () => {
    expect(formatConfidence(0.85)).toBe("85%");
  });
});

// ---------------------------------------------------------------------------
// Status mapping
// ---------------------------------------------------------------------------

describe("statusToTone", () => {
  it("maps completed to success", () => {
    expect(statusToTone("completed")).toBe("success");
  });

  it("maps running to info", () => {
    expect(statusToTone("running")).toBe("info");
  });

  it("maps failed to danger", () => {
    expect(statusToTone("failed")).toBe("danger");
  });

  it("maps unknown to neutral", () => {
    expect(statusToTone("some_unknown")).toBe("neutral");
  });
});

describe("stageStatusToTone", () => {
  it("maps completed to success", () => {
    expect(stageStatusToTone("completed")).toBe("success");
  });

  it("maps running to info", () => {
    expect(stageStatusToTone("running")).toBe("info");
  });

  it("maps anything else to neutral", () => {
    expect(stageStatusToTone("pending")).toBe("neutral");
  });
});

// ---------------------------------------------------------------------------
// Section mappers
// ---------------------------------------------------------------------------

const MOCK_DATA: ExecutiveReportData = {
  workflow_id: "wf-1",
  workflow_title: "Test Workflow",
  original_prompt: "Build a feature for X",
  workflow_type: "planning",
  status: "approved",
  current_stage: "engineering_review",
  created_at: "2025-01-01T00:00:00Z",
  completed_at: "2025-01-01T01:00:00Z",
  duration_ms: 3600000,
  approved_by: "admin@example.com",
  total_tokens: 15000,
  total_cost_usd: 0.25,
  total_llm_calls: 6,
  primary_model: "gpt-4o",
  primary_provider: "openai",
  overall_confidence: 0.87,
  stages: [
    {
      stage: "context_discovery",
      status: "completed",
      duration_ms: 5000,
      confidence_score: 0.9,
      model: "gpt-4o",
      provider: "openai",
      prompt_tokens: 1000,
      completion_tokens: 500,
      total_tokens: 1500,
      estimated_cost_usd: 0.03,
      latency_ms: 2000,
      retry_count: 0,
    },
    {
      stage: "planning",
      status: "completed",
      duration_ms: 8000,
      confidence_score: 0.85,
      model: "gpt-4o",
      provider: "openai",
      prompt_tokens: 2000,
      completion_tokens: 1000,
      total_tokens: 3000,
      estimated_cost_usd: 0.06,
      latency_ms: 3000,
      retry_count: 0,
    },
  ],
  repository_impact: {
    repositories_affected: ["repo-a", "repo-b"],
    files_changed: 12,
    components_affected: ["AuthService", "UserController"],
    dependency_impact: ["express@5.0"],
  },
  review_results: [
    { category: "Security", status: "pass", summary: "No issues found", issues: [] },
    { category: "Performance", status: "warning", summary: "Possible N+1 query", issues: ["N+1"] },
  ],
  recommendations: {
    merge_readiness: "conditional",
    risks: ["Performance regression risk"],
    next_actions: ["Add index on users table"],
    blocking_items: [],
  },
};

describe("mapSummary", () => {
  it("maps to presentation props", () => {
    const result = mapSummary(MOCK_DATA);
    expect(result.title).toBe("Test Workflow");
    expect(result.status).toBe("approved");
    expect(result.statusTone).toBe("success");
    expect(result.duration).toBe("60.0m");
    expect(result.cost).toBe("$0.25");
    expect(result.tokens).toBe("15.0K");
    expect(result.confidence).toBe("87%");
    expect(result.approvedBy).toBe("admin@example.com");
  });
});

describe("mapTimeline", () => {
  it("produces all 6 standard stages", () => {
    const result = mapTimeline(MOCK_DATA);
    expect(result).toHaveLength(6);
  });

  it("marks completed stages correctly", () => {
    const result = mapTimeline(MOCK_DATA);
    expect(result[0].status).toBe("completed"); // context_discovery
    expect(result[1].status).toBe("completed"); // planning
    expect(result[2].status).toBe("pending"); // development (not in data)
  });

  it("generates human-readable labels", () => {
    const result = mapTimeline(MOCK_DATA);
    expect(result[0].label).toBe("Context Discovery");
    expect(result[4].label).toBe("Documentation Planning");
  });
});

describe("mapMetrics", () => {
  it("maps primary model and provider", () => {
    const result = mapMetrics(MOCK_DATA);
    expect(result.primaryModel).toBe("gpt-4o");
    expect(result.primaryProvider).toBe("openai");
    expect(result.totalCalls).toBe(6);
  });

  it("produces a row per stage", () => {
    const result = mapMetrics(MOCK_DATA);
    expect(result.rows).toHaveLength(2);
    expect(result.rows[0].stage).toBe("Context Discovery");
    expect(result.rows[0].tokens).toBe("1.5K");
  });
});

describe("mapCharts", () => {
  it("produces chart data for each metric type", () => {
    const result = mapCharts(MOCK_DATA);
    expect(result.duration).toHaveLength(2);
    expect(result.tokens).toHaveLength(2);
    expect(result.cost).toHaveLength(2);
    expect(result.duration[0].value).toBe(5000);
    expect(result.tokens[1].value).toBe(3000);
  });
});

describe("mapRepositoryImpact", () => {
  it("maps counts and lists", () => {
    const result = mapRepositoryImpact(MOCK_DATA);
    expect(result.repositoryCount).toBe(2);
    expect(result.filesChanged).toBe(12);
    expect(result.componentCount).toBe(2);
    expect(result.repositories).toContain("repo-a");
    expect(result.dependencies).toContain("express@5.0");
  });
});

describe("mapReviewResults", () => {
  it("maps review categories with tones", () => {
    const result = mapReviewResults(MOCK_DATA);
    expect(result).toHaveLength(2);
    expect(result[0].category).toBe("Security");
    expect(result[0].statusTone).toBe("success");
    expect(result[1].category).toBe("Performance");
    expect(result[1].statusTone).toBe("warning");
  });
});

describe("mapRecommendations", () => {
  it("maps merge readiness and lists", () => {
    const result = mapRecommendations(MOCK_DATA);
    expect(result.mergeReadiness).toBe("conditional");
    expect(result.mergeReadinessTone).toBe("warning");
    expect(result.risks).toContain("Performance regression risk");
    expect(result.nextActions).toContain("Add index on users table");
    expect(result.blockingItems).toHaveLength(0);
  });
});
