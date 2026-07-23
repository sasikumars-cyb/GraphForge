import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunProgress } from "./RunProgress";

describe("RunProgress", () => {
  it("shows queued message", () => {
    render(<RunProgress status="queued" />);
    expect(screen.getByText(/Queued/)).toBeInTheDocument();
  });

  it("shows running message", () => {
    render(<RunProgress status="running" />);
    expect(screen.getByText(/Running/)).toBeInTheDocument();
  });

  it("shows completed message", () => {
    render(<RunProgress status="completed" />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("shows failed message with error", () => {
    render(<RunProgress status="failed" error="Something went wrong" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("has accessible status role", () => {
    render(<RunProgress status="running" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
