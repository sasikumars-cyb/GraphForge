import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AskAnswer, type DisplayAnswer } from "./AskAnswer";

/**
 * C-1 / P0.4 frontend states. The audit's worst outcome was a wrong
 * repository presented *with evidence badges*, so these tests pin the
 * negative case too: an ungrounded turn must show no provenance at all.
 */

function answer(overrides: Partial<DisplayAnswer> = {}): DisplayAnswer {
  return {
    answer: "an answer",
    why: "",
    evidence: [],
    actions: [],
    ...overrides,
  };
}

function renderAnswer(data: DisplayAnswer, onSelectCandidate?: (name: string) => void) {
  return render(
    <MemoryRouter>
      <AskAnswer data={data} onSelectCandidate={onSelectCandidate} />
    </MemoryRouter>,
  );
}

describe("AskAnswer — clarification state", () => {
  const clarification = answer({
    answer: "I couldn't confidently tell which system you mean — did you mean A or B?",
    needsClarification: true,
    candidates: [
      { name: "billing-data-service", full_name: "acme/billing-data-service", repository_id: "1" },
      { name: "shipping-data-service", full_name: "acme/shipping-data-service", repository_id: "2" },
    ],
  });

  it("offers the candidate repositories to pick from", () => {
    renderAnswer(clarification);
    expect(screen.getByText("Did you mean")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "billing-data-service" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "shipping-data-service" })).toBeInTheDocument();
  });

  it("shows NO evidence badges for an ungrounded turn", () => {
    renderAnswer(clarification);
    expect(screen.queryByText("Evidence")).not.toBeInTheDocument();
    expect(screen.queryByText(/Dependency Graph/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Blast radius calculated/i)).not.toBeInTheDocument();
  });

  it("reports the chosen repository back to the caller", async () => {
    const onSelect = vi.fn();
    renderAnswer(clarification, onSelect);
    await userEvent.click(screen.getByRole("button", { name: "billing-data-service" }));
    expect(onSelect).toHaveBeenCalledWith("billing-data-service");
  });
});

describe("AskAnswer — grounded impact", () => {
  const grounded = answer({
    answer: "Impact assessment — Medium.",
    why: "reaches 1 other tracked repository",
    evidence: [{ source: "Dependency Graph", provenance: "derived" }],
    impact: { severity: "medium", summary: "1 downstream repository", affected: ["acme/other"] },
  });

  it("shows evidence and the blast-radius provenance when grounded", () => {
    renderAnswer(grounded);
    expect(screen.getByText("Evidence")).toBeInTheDocument();
    expect(screen.getByText("Dependency Graph")).toBeInTheDocument();
  });

  it("marks a truncated blast radius as partial, never exhaustive", async () => {
    renderAnswer(answer({ ...grounded, truncated: true }));
    // The provenance line lives under the collapsed "Why" disclosure.
    await userEvent.click(screen.getByRole("button", { name: /why/i }));
    expect(screen.getByText(/Partial blast radius/i)).toBeInTheDocument();
    expect(screen.queryByText(/Blast radius calculated/i)).not.toBeInTheDocument();
  });

  it("describes a complete blast radius without the partial caveat", async () => {
    renderAnswer(grounded);
    await userEvent.click(screen.getByRole("button", { name: /why/i }));
    expect(screen.getByText(/Blast radius calculated/i)).toBeInTheDocument();
    expect(screen.queryByText(/Partial blast radius/i)).not.toBeInTheDocument();
  });
});
