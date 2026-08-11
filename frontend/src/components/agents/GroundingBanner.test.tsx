import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { GroundingBanner } from "./GroundingBanner";

/**
 * UX audit P1.3/P1.4 regression coverage: grounded and ungrounded states
 * must render visibly differently, and the two distinct ungrounded states
 * (a genuine infrastructure failure vs. nothing indexed yet) must never
 * collapse into the same, sometimes-inaccurate copy again.
 */
function renderBanner(props: Parameters<typeof GroundingBanner>[0]) {
  return render(
    <MemoryRouter>
      <GroundingBanner {...props} />
    </MemoryRouter>,
  );
}

describe("GroundingBanner", () => {
  it("shows the grounded state when grounding_status is 'grounded'", () => {
    renderBanner({
      graphContextUsed: true,
      groundingStatus: "grounded",
      repositoriesConsulted: ["order-service"],
      subject: "plan",
    });
    expect(screen.getByText("Grounded in your architecture")).toBeInTheDocument();
  });

  it("shows an infrastructure-failure explanation — not the greenfield one — when grounding_status is 'unavailable'", () => {
    renderBanner({
      graphContextUsed: false,
      groundingStatus: "unavailable",
      repositoriesConsulted: [],
      subject: "plan",
    });
    expect(screen.getByText("Architecture graph unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No codebase context")).not.toBeInTheDocument();
    // The old bug: this exact condition used to render "expected for a new
    // project" copy, which is wrong for a genuine infra failure.
    expect(screen.queryByText(/expected for a new project/)).not.toBeInTheDocument();
    // Indexing a repository doesn't fix an infra outage — no such action.
    expect(screen.queryByRole("link", { name: "Index a repository" })).not.toBeInTheDocument();
  });

  it("shows the not-indexed state, with an Index a repository action, when grounding_status is 'not_indexed'", () => {
    renderBanner({
      graphContextUsed: false,
      groundingStatus: "not_indexed",
      repositoriesConsulted: [],
      subject: "plan",
    });
    expect(screen.getByText("No codebase context")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Index a repository" })).toBeInTheDocument();
  });

  it("falls back to the legacy two-signal heuristic when grounding_status is absent (pre-fix persisted results)", () => {
    renderBanner({
      graphContextUsed: true,
      repositoriesConsulted: ["order-service"],
      subject: "plan",
    });
    expect(screen.getByText("Grounded in your architecture")).toBeInTheDocument();
  });

  it("collapses the repository list behind a disclosure instead of always showing it", () => {
    renderBanner({
      graphContextUsed: true,
      groundingStatus: "grounded",
      repositoriesConsulted: ["order-service", "payment-service"],
      subject: "plan",
    });
    // The names exist in the DOM (inside <details>) but aren't visible
    // until expanded — assert the disclosure control exists rather than
    // asserting visibility, since jsdom doesn't compute layout/`hidden`.
    expect(screen.getByText("Show repositories")).toBeInTheDocument();
  });
});
