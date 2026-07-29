import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KnowledgeSourcesPanel } from "./KnowledgeSourcesPanel";
import type { Evidence, PlanningResult } from "../../types/agent";

const baseResult: PlanningResult = {
  executive_summary: "",
  implementation_steps: [],
  affected_components: [],
  kafka_topics_involved: [],
  risk_considerations: [],
  graph_context_used: true,
  repositories_consulted: ["svc-a"],
};

function confluenceEvidence(overrides: Partial<Evidence>): Evidence {
  return { kind: "tool_call", reference: "confluence_context", summary: "", ...overrides };
}

describe("KnowledgeSourcesPanel — Confluence status distinctions", () => {
  it("shows a found/connected state when content was actually retrieved", () => {
    render(
      <KnowledgeSourcesPanel
        result={baseResult}
        evidence={[confluenceEvidence({ summary: "1 relevant page found.", status: "success" })]}
      />,
    );
    expect(screen.getByText("1 relevant page found.")).toBeInTheDocument();
  });

  it("does NOT render a success indicator for 'no relevant content found' — the bug this fixes", () => {
    render(
      <KnowledgeSourcesPanel
        result={baseResult}
        evidence={[
          confluenceEvidence({
            summary: "No relevant Confluence content found.",
            status: "not_found",
          }),
        ]}
      />,
    );
    const row = screen.getByText("No relevant Confluence content found.").closest("li");
    expect(row?.querySelector(".bg-emerald-400")).toBeNull();
  });

  it("distinguishes 'unavailable' (not configured) from 'not_found' (searched, nothing relevant)", () => {
    render(
      <KnowledgeSourcesPanel
        result={baseResult}
        evidence={[
          confluenceEvidence({ summary: "Confluence is not connected.", status: "unavailable" }),
        ]}
      />,
    );
    expect(screen.getByText("Confluence is not connected.")).toBeInTheDocument();
  });

  it("shows a failed state distinctly (rose, not just absent)", () => {
    render(
      <KnowledgeSourcesPanel
        result={baseResult}
        evidence={[confluenceEvidence({ summary: "FAILED: timeout", status: "failed" })]}
      />,
    );
    const row = screen.getByText("timeout").closest("li");
    expect(row?.querySelector(".text-rose-400")).not.toBeNull();
  });

  it("falls back to text-based inference for legacy evidence with no status field", () => {
    // Older, already-persisted runs stored evidence before `status` existed.
    render(
      <KnowledgeSourcesPanel
        result={baseResult}
        evidence={[
          {
            kind: "tool_call",
            reference: "confluence_context",
            summary: "No relevant Confluence content found.",
          },
        ]}
      />,
    );
    const row = screen.getByText("No relevant Confluence content found.").closest("li");
    expect(row?.querySelector(".bg-emerald-400")).toBeNull();
  });
});
