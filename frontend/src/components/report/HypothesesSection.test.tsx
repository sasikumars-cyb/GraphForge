import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { HypothesesSectionVM } from "../../lib/api/reports";
import { HypothesesSection } from "./HypothesesSection";

function section(overrides: Partial<HypothesesSectionVM>): HypothesesSectionVM {
  return {
    availability: { status: "available", reason: null },
    synthesis_state: "completed",
    items: [],
    truncated_count: 0,
    ...overrides,
  };
}

describe("HypothesesSection — degraded-state matrix", () => {
  it("NOT_RUN never reads as 'no hypotheses found'", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          synthesis_state: "not_run",
          availability: { status: "unavailable", reason: "not recorded" },
        })}
      />,
    );
    expect(screen.getByText(/was not recorded/i)).toBeInTheDocument();
    expect(screen.queryByText(/converged without competing/i)).not.toBeInTheDocument();
  });

  it("FAILED is distinguishable from NOT_RUN and from an empty result", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          synthesis_state: "failed",
          availability: { status: "degraded", reason: "failed" },
        })}
      />,
    );
    expect(screen.getByText(/reasoning synthesis failed/i)).toBeInTheDocument();
  });

  it("COMPLETED_EMPTY reads as a real, positive outcome", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          synthesis_state: "completed_empty",
          availability: { status: "available", reason: null },
        })}
      />,
    );
    expect(screen.getByText(/converged without competing hypotheses/i)).toBeInTheDocument();
  });

  it("renders a single hypothesis without grid awkwardness", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          items: [
            {
              entry: {
                statement: "The bug is in the parser",
                status: "supported",
                confidence: 0.8,
                supporting_evidence: ["trace"],
                contradicting_evidence: [],
              },
              verification_status: null,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("The bug is in the parser")).toBeInTheDocument();
    expect(screen.getByText("Supported")).toBeInTheDocument();
    expect(screen.getByText("Not checked")).toBeInTheDocument();
  });

  it("renders many hypotheses and reports the truncated count", () => {
    const items = Array.from({ length: 4 }, (_, i) => ({
      entry: {
        statement: `hypothesis ${i}`,
        status: "unknown" as const,
        confidence: 0.1 * i,
        supporting_evidence: [],
        contradicting_evidence: [],
      },
      verification_status: null,
    }));
    render(<HypothesesSection hypotheses={section({ items, truncated_count: 2 })} />);
    items.forEach((item) => {
      expect(screen.getByText(item.entry.statement)).toBeInTheDocument();
    });
    expect(screen.getByText(/\+ 2 more, lower confidence/)).toBeInTheDocument();
  });

  it("shows SUPPORTED + VERIFIED as two distinct badges, never merged", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          items: [
            {
              entry: {
                statement: "h",
                status: "supported",
                confidence: 0.9,
                supporting_evidence: [],
                contradicting_evidence: [],
              },
              verification_status: "verified",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Supported")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("shows SUPPORTED + UNVERIFIED as two distinct badges", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          items: [
            {
              entry: {
                statement: "h",
                status: "supported",
                confidence: 0.9,
                supporting_evidence: [],
                contradicting_evidence: [],
              },
              verification_status: "unverified",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Supported")).toBeInTheDocument();
    expect(screen.getByText("Unverified")).toBeInTheDocument();
  });

  it("shows INFERRED + NOT_CHECKED as two distinct badges", () => {
    render(
      <HypothesesSection
        hypotheses={section({
          items: [
            {
              entry: {
                statement: "h",
                status: "inferred",
                confidence: 0.4,
                supporting_evidence: [],
                contradicting_evidence: [],
              },
              verification_status: null,
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Inferred")).toBeInTheDocument();
    expect(screen.getByText("Not checked")).toBeInTheDocument();
  });
});
