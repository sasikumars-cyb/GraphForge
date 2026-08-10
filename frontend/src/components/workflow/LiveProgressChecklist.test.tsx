import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

describe("LiveProgressChecklist — P2 fix: elapsed time on a long-running active step", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows no elapsed-time badge in the first few seconds", () => {
    const progress: LiveProgress = {
      iteration: 8,
      max_iterations: 8,
      steps: [{ label: "Synthesizing findings", status: "active" }],
    };
    render(<LiveProgressChecklist progress={progress} />);
    expect(screen.queryByText(/\ds$/)).not.toBeInTheDocument();
  });

  it("shows a real, ticking elapsed-time badge once the active step has run a while", () => {
    const progress: LiveProgress = {
      iteration: 8,
      max_iterations: 8,
      steps: [{ label: "Synthesizing findings", status: "active" }],
    };
    render(<LiveProgressChecklist progress={progress} />);

    act(() => {
      vi.advanceTimersByTime(7000);
    });
    expect(screen.getByText("7s")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText("12s")).toBeInTheDocument();
  });

  it("resets the elapsed time when the active step changes — never claims a new step is old", () => {
    const first: LiveProgress = {
      iteration: 7,
      max_iterations: 8,
      steps: [{ label: "Checking documentation", status: "active" }],
    };
    const { rerender } = render(<LiveProgressChecklist progress={first} />);
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByText("10s")).toBeInTheDocument();

    const next: LiveProgress = {
      iteration: 8,
      max_iterations: 8,
      steps: [
        { label: "Checking documentation", status: "done" },
        { label: "Synthesizing findings", status: "active" },
      ],
    };
    rerender(<LiveProgressChecklist progress={next} />);
    expect(screen.queryByText(/\ds$/)).not.toBeInTheDocument();
  });
});
