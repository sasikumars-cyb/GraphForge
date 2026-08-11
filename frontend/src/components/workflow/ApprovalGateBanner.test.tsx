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
        refineObjective="Implement JWT auth"
        workflowId="wf-1"
        isSubmitting={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
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

  it("calls onReject (a real, persisted rejection — see the component's own docstring) when Reject is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    const onReject = vi.fn();
    renderBanner({ onApprove, onReject });
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onReject).toHaveBeenCalledTimes(1);
    expect(onApprove).not.toHaveBeenCalled();
  });

  it("disables all actions while submitting", () => {
    renderBanner({ isSubmitting: true });
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refine" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Starting…/ })).toBeDisabled();
  });
});

describe("ApprovalGateBanner — failure variant", () => {
  it("shows Retry Stage and Refine instead of Approve/Reject", () => {
    renderBanner({ failure: { stage: "testing", errorMessage: "boom" } });
    expect(screen.getByText(/Testing/)).toBeInTheDocument();
    expect(screen.getByText(/failed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry Stage/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refine" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });

  it("calls onApprove (as a retry) when Retry Stage is clicked", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    renderBanner({ onApprove, failure: { stage: "testing", errorMessage: "boom" } });
    await user.click(screen.getByRole("button", { name: /Retry Stage/ }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("hides the raw error message until 'View error details' is expanded", () => {
    renderBanner({ failure: { stage: "testing", errorMessage: "raw stack trace text" } });
    // The <details> content exists in the DOM (jsdom doesn't hide it from
    // queries), but it must sit behind a "View error details" disclosure
    // rather than being shown as part of the main message.
    expect(screen.getByText("View error details")).toBeInTheDocument();
    expect(screen.getByText("raw stack trace text").closest("details")).not.toBeNull();
  });

  it("omits the error-details disclosure when there is no error message to show", () => {
    renderBanner({ failure: { stage: "testing", errorMessage: null } });
    expect(screen.queryByText("View error details")).not.toBeInTheDocument();
  });
});
