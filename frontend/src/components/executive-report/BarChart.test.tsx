import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart } from "./BarChart";
import type { ChartBar } from "../../lib/executiveReportMapper";

describe("BarChart", () => {
  const sampleBars: ChartBar[] = [
    { label: "Planning", value: 8000, formatted: "8.0s" },
    { label: "Development", value: 4000, formatted: "4.0s" },
    { label: "Testing", value: 2000, formatted: "2.0s" },
  ];

  it("renders the title", () => {
    render(<BarChart title="Execution Time" bars={sampleBars} />);
    expect(screen.getByText("Execution Time")).toBeInTheDocument();
  });

  it("renders a label for each bar", () => {
    render(<BarChart title="Test" bars={sampleBars} />);
    expect(screen.getByText("Planning")).toBeInTheDocument();
    expect(screen.getByText("Development")).toBeInTheDocument();
    expect(screen.getByText("Testing")).toBeInTheDocument();
  });

  it("renders formatted values", () => {
    render(<BarChart title="Test" bars={sampleBars} />);
    expect(screen.getByText("8.0s")).toBeInTheDocument();
    expect(screen.getByText("4.0s")).toBeInTheDocument();
    expect(screen.getByText("2.0s")).toBeInTheDocument();
  });

  it("scales bars proportionally — max bar is 100%", () => {
    const { container } = render(<BarChart title="Test" bars={sampleBars} />);
    // The first bar (8000) should have width: 100%
    const barFills = container.querySelectorAll("[role='presentation']");
    expect(barFills).toHaveLength(3);
    // Max bar (Planning: 8000) → 100%
    expect(barFills[0]).toHaveStyle({ width: "100.0%" });
    // Half (Development: 4000) → 50%
    expect(barFills[1]).toHaveStyle({ width: "50.0%" });
  });

  it("handles empty bars array", () => {
    const { container } = render(<BarChart title="Empty" bars={[]} />);
    expect(screen.getByText("Empty")).toBeInTheDocument();
    expect(container.querySelectorAll("[role='presentation']")).toHaveLength(0);
  });

  it("handles all-zero values without division errors", () => {
    const zeroBars: ChartBar[] = [
      { label: "A", value: 0, formatted: "0" },
      { label: "B", value: 0, formatted: "0" },
    ];
    const { container } = render(<BarChart title="Zero" bars={zeroBars} />);
    const barFills = container.querySelectorAll("[role='presentation']");
    expect(barFills).toHaveLength(2);
    // With 0/max(0,1) = 0, minimum is 1%
    expect(barFills[0]).toHaveStyle({ width: "1%" });
  });
});
