import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConfidenceBadge } from "./ConfidenceBadge";

describe("ConfidenceBadge", () => {
  it("renders high confidence with percentage", () => {
    render(<ConfidenceBadge confidence={{ score: 0.88, reasoning: "Strong evidence" }} />);
    expect(screen.getByText("88%")).toBeInTheDocument();
  });

  it("renders low confidence with percentage", () => {
    render(<ConfidenceBadge confidence={{ score: 0.3, reasoning: "Weak evidence" }} />);
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("renders null score as dash", () => {
    render(<ConfidenceBadge confidence={{ score: null, reasoning: "" }} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows reasoning when showReasoning is true", () => {
    render(
      <ConfidenceBadge
        confidence={{ score: 0.85, reasoning: "Graph data confirms impact" }}
        showReasoning
      />,
    );
    expect(screen.getByText("Graph data confirms impact")).toBeInTheDocument();
  });

  it("hides reasoning by default", () => {
    render(
      <ConfidenceBadge confidence={{ score: 0.85, reasoning: "Hidden reasoning" }} />,
    );
    expect(screen.queryByText("Hidden reasoning")).not.toBeInTheDocument();
  });

  it("has correct aria-label", () => {
    render(<ConfidenceBadge confidence={{ score: 0.72, reasoning: "" }} />);
    expect(screen.getByLabelText("Confidence: 72%")).toBeInTheDocument();
  });
});
