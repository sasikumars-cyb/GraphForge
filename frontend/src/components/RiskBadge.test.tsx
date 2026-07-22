import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge } from "./RiskBadge";

describe("RiskBadge", () => {
  it.each([
    ["critical", "Critical"],
    ["high", "High"],
    ["medium", "Medium"],
    ["low", "Low"],
  ] as const)("renders the %s level as %s", (level, expectedLabel) => {
    render(<RiskBadge level={level} />);
    expect(screen.getByText(expectedLabel)).toBeInTheDocument();
  });
});
