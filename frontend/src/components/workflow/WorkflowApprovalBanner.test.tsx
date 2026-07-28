import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { WorkflowApprovalBanner } from "./WorkflowApprovalBanner";

function renderBanner(props: Partial<Parameters<typeof WorkflowApprovalBanner>[0]> = {}) {
  return render(
    <MemoryRouter>
      <WorkflowApprovalBanner
        workflowTitle="Add rate limiting"
        workflowId="wf-1"
        status="awaiting_approval"
        isSubmitting={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("WorkflowApprovalBanner — awaiting_approval", () => {
  it("shows Approve Blueprint and Reject actions", () => {
    renderBanner();
    expect(screen.getByRole("button", { name: /Approve Blueprint/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refine" })).toBeInTheDocument();
  });

  it("calls onApprove when Approve Blueprint is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    renderBanner({ onApprove });
    await user.click(screen.getByRole("button", { name: /Approve Blueprint/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("calls onReject when Reject is clicked", async () => {
    const user = userEvent.setup();
    const onReject = vi.fn();
    renderBanner({ onReject });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("disables all actions while submitting", () => {
    renderBanner({ isSubmitting: true });
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refine" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Submitting…/ })).toBeDisabled();
  });
});

describe("WorkflowApprovalBanner — terminal states", () => {
  it("shows a read-only confirmation once approved, no action buttons", () => {
    renderBanner({ status: "approved" });
    expect(screen.getByText(/Blueprint approved/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve Blueprint/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("never leaks backend implementation terminology in the approved copy", () => {
    renderBanner({ status: "approved" });
    expect(screen.queryByText(/Auto Execution/)).not.toBeInTheDocument();
    expect(screen.queryByText(/this build/)).not.toBeInTheDocument();
  });

  it("shows a read-only confirmation once rejected, no action buttons", () => {
    renderBanner({ status: "rejected" });
    expect(screen.getByText(/was rejected/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve Blueprint/ })).not.toBeInTheDocument();
  });
});
