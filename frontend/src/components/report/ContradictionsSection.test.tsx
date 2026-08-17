import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ContradictionsSectionVM } from "../../lib/api/reports";
import { ContradictionsSection } from "./ContradictionsSection";

function section(overrides: Partial<ContradictionsSectionVM>): ContradictionsSectionVM {
  return {
    availability: { status: "available", reason: null },
    synthesis_state: "completed",
    items: [],
    ...overrides,
  };
}

describe("ContradictionsSection", () => {
  it("NOT_RUN shows the not-recorded notice, not 'no contradictions'", () => {
    render(
      <ContradictionsSection
        contradictions={section({
          synthesis_state: "not_run",
          availability: { status: "unavailable", reason: "x" },
        })}
      />,
    );
    expect(screen.getByText(/was not recorded/i)).toBeInTheDocument();
  });

  it("COMPLETED with zero items reads as a genuine 'no conflicts found'", () => {
    render(<ContradictionsSection contradictions={section({})} />);
    expect(screen.getByText(/no contradictions found/i)).toBeInTheDocument();
  });

  it("COMPLETED_EMPTY also reads as 'no conflicts found', never the hypotheses-specific copy", () => {
    // Regression: COMPLETED_EMPTY previously fell through to the shared
    // SynthesisStateNotice, which reads "converged without competing
    // hypotheses" — correct for Hypotheses, wrong for Contradictions.
    render(
      <ContradictionsSection contradictions={section({ synthesis_state: "completed_empty" })} />,
    );
    expect(screen.getByText(/no contradictions found/i)).toBeInTheDocument();
    expect(screen.queryByText(/competing hypotheses/i)).not.toBeInTheDocument();
  });

  it("renders an unresolved contradiction with both evidence columns, its impact, and its required resolution", () => {
    render(
      <ContradictionsSection
        contradictions={section({
          items: [
            {
              entry: {
                statement: "The ticket implies a timeout exists",
                evidence_for: ["title says timeout-bump"],
                evidence_against: ["no timeout symbol found"],
                resolved: false,
                resolution_note: "",
              },
              is_blocking: true,
              impact: "Blocks the outcome: the conclusion cannot be relied on.",
              required_resolution: "Establish which side is correct.",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("The ticket implies a timeout exists")).toBeInTheDocument();
    expect(screen.getByText(/title says timeout-bump/)).toBeInTheDocument();
    expect(screen.getByText(/no timeout symbol found/)).toBeInTheDocument();
    // An unresolved contradiction is never rendered as merely "unresolved"
    // — it is a blocking item, the same way `next_actions` counts it.
    expect(screen.getByText("Unresolved — blocking")).toBeInTheDocument();
    expect(screen.getByText(/Blocks the outcome/)).toBeInTheDocument();
    expect(screen.getByText("Establish which side is correct.")).toBeInTheDocument();
    expect(screen.getByText(/1 unresolved and blocking/)).toBeInTheDocument();
  });

  it("renders a resolved contradiction with its resolution note and no blocking framing", () => {
    render(
      <ContradictionsSection
        contradictions={section({
          items: [
            {
              entry: {
                statement: "Two sources disagreed",
                evidence_for: ["a"],
                evidence_against: ["b"],
                resolved: true,
                resolution_note: "Later evidence confirmed source A.",
              },
              is_blocking: false,
              impact: "Resolved — this no longer affects the conclusion.",
              required_resolution: "None; already settled.",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(screen.getByText("Later evidence confirmed source A.")).toBeInTheDocument();
    expect(screen.getByText(/all resolved/)).toBeInTheDocument();
  });
});
