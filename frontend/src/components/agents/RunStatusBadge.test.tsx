import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunStatusBadge } from "./RunStatusBadge";

describe("RunStatusBadge", () => {
  it("renders completed status", () => {
    render(<RunStatusBadge status="completed" />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("renders failed status", () => {
    render(<RunStatusBadge status="failed" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders running status", () => {
    render(<RunStatusBadge status="running" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("renders queued status", () => {
    render(<RunStatusBadge status="queued" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("renders partial status", () => {
    render(<RunStatusBadge status="partial" />);
    expect(screen.getByText("Partial")).toBeInTheDocument();
  });
});
