import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LiveProgress } from "../../types/agent";
import { LiveProgressChecklist } from "./LiveProgressChecklist";

describe("LiveProgressChecklist", () => {
  it("renders nothing when there are no steps yet", () => {
    const progress: LiveProgress = { iteration: 0, max_iterations: 8, steps: [] };
    const { container } = render(<LiveProgressChecklist progress={progress} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows completed steps with a check and the active step with a spinner", () => {
    const progress: LiveProgress = {
      iteration: 3,
      max_iterations: 8,
      steps: [
        { label: "Parsing the request", status: "done" },
        { label: "Checking Jira", status: "done" },
        { label: "Investigating the architecture", status: "active" },
      ],
    };
    render(<LiveProgressChecklist progress={progress} />);
    expect(screen.getByText("Parsing the request")).toBeInTheDocument();
    expect(screen.getByText("Checking Jira")).toBeInTheDocument();
    expect(screen.getByText("Investigating the architecture")).toBeInTheDocument();
    expect(screen.getByText("step 3 of 8")).toBeInTheDocument();
  });

  it("never renders a step this component wasn't given — no fabricated pending items", () => {
    const progress: LiveProgress = {
      iteration: 1,
      max_iterations: 8,
      steps: [{ label: "Parsing the request", status: "active" }],
    };
    render(<LiveProgressChecklist progress={progress} />);
    // Exactly the one real step — nothing invented to fill out the list.
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("Parsing the request")).toBeInTheDocument();
    expect(screen.queryByText(/Checking documentation/)).not.toBeInTheDocument();
  });
});
