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

  it("renders an unresolved contradiction with both evidence columns", () => {
    render(
      <ContradictionsSection
        contradictions={section({
          items: [
            {
              statement: "The ticket implies a timeout exists",
              evidence_for: ["title says timeout-bump"],
              evidence_against: ["no timeout symbol found"],
              resolved: false,
              resolution_note: "",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("The ticket implies a timeout exists")).toBeInTheDocument();
    expect(screen.getByText(/title says timeout-bump/)).toBeInTheDocument();
    expect(screen.getByText(/no timeout symbol found/)).toBeInTheDocument();
    expect(screen.getByText("Unresolved")).toBeInTheDocument();
  });

  it("renders a resolved contradiction with its resolution note", () => {
    render(
      <ContradictionsSection
        contradictions={section({
          items: [
            {
              statement: "Two sources disagreed",
              evidence_for: ["a"],
              evidence_against: ["b"],
              resolved: true,
              resolution_note: "Later evidence confirmed source A.",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(screen.getByText("Later evidence confirmed source A.")).toBeInTheDocument();
  });
});
