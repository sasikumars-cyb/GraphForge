import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ApprovalGateBanner } from "./ApprovalGateBanner";

function renderBanner(props: Partial<Parameters<typeof ApprovalGateBanner>[0]> = {}) {
  return render(
    <MemoryRouter>
      <ApprovalGateBanner
        completedStage="planning"
        nextStage="development"
        workflowTitle="Implement JWT auth"
        isSubmitting={false}
        onApprove={vi.fn()}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("ApprovalGateBanner", () => {
  it("names the completed and next stage", () => {
    renderBanner();
    expect(screen.getByText("Planning")).toBeInTheDocument();
    expect(screen.getByText("Development")).toBeInTheDocument();
  });

  it("calls onApprove when Approve & Continue is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    renderBanner({ onApprove });
    await user.click(screen.getByRole("button", { name: /Approve & Continue/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("does not call any API — Reject is a local, non-mutating UI state", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    renderBanner({ onApprove });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onApprove).not.toHaveBeenCalled();
    expect(screen.getByText(/left at/)).toBeInTheDocument();
  });

  it("disables all actions while submitting", () => {
    renderBanner({ isSubmitting: true });
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit Workflow" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Starting…/ })).toBeDisabled();
  });
});
