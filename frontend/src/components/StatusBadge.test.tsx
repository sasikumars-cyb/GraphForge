import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders the given label regardless of tone", () => {
    render(<StatusBadge label="Merged" tone="success" />);
    expect(screen.getByText("Merged")).toBeInTheDocument();
  });
});
