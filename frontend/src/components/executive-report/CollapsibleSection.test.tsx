import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CollapsibleSection } from "./CollapsibleSection";

describe("CollapsibleSection", () => {
  it("renders title and children when defaultOpen is true", () => {
    render(
      <CollapsibleSection title="Test Section" defaultOpen>
        <p>Section content</p>
      </CollapsibleSection>,
    );

    expect(screen.getByText("Test Section")).toBeInTheDocument();
    expect(screen.getByText("Section content")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("hides children when defaultOpen is false", () => {
    render(
      <CollapsibleSection title="Hidden Section" defaultOpen={false}>
        <p>Hidden content</p>
      </CollapsibleSection>,
    );

    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    // Content exists in DOM but is visually hidden via max-h-0
    const contentWrapper = screen.getByText("Hidden content").closest("[aria-hidden]");
    expect(contentWrapper).toHaveAttribute("aria-hidden", "true");
  });

  it("toggles content visibility on click", async () => {
    const user = userEvent.setup();
    render(
      <CollapsibleSection title="Toggle Section">
        <p>Toggle me</p>
      </CollapsibleSection>,
    );

    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "true");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "false");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("defaults to open when defaultOpen is not specified", () => {
    render(
      <CollapsibleSection title="Default Open">
        <p>Should be visible</p>
      </CollapsibleSection>,
    );

    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });
});
