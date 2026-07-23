import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "../app/auth-context";
import { WorkflowTimeline } from "../components/workflow/WorkflowTimeline";
import { StageNavigation } from "../components/workflow/StageNavigation";
import { NewWorkflowPage } from "./WorkflowPage";
import type { WorkflowStageInfo } from "../types/agent";

// Mock workflows API
vi.mock("../lib/api/workflows", () => ({
  createWorkflow: vi.fn(),
  getWorkflow: vi.fn(),
  continueWorkflow: vi.fn(),
  listWorkflows: vi.fn(),
}));

function renderWithAuth(ui: React.ReactElement, authValue?: Partial<AuthContextValue>) {
  const defaultAuth: AuthContextValue = {
    user: { id: "u1", email: "test@test.com", full_name: "Test User", is_active: true },
    token: "test-token",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
    ...authValue,
  };

  return render(
    <AuthContext.Provider value={defaultAuth}>
      <MemoryRouter>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("WorkflowTimeline", () => {
  const stages: WorkflowStageInfo[] = [
    { stage: "planning", label: "Planning", status: "completed", run_id: "run-1" },
    { stage: "development", label: "Development", status: "running", run_id: "run-2" },
    { stage: "testing", label: "Testing", status: "pending", run_id: null },
    { stage: "review", label: "Review", status: "pending", run_id: null },
  ];

  it("renders all stage labels", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    expect(screen.getByText("Planning")).toBeInTheDocument();
    expect(screen.getByText("Development")).toBeInTheDocument();
    expect(screen.getByText("Testing")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("renders completed stage as a link", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    const link = screen.getByRole("link", { name: "Planning: completed" });
    expect(link).toHaveAttribute("href", "/runs/run-1");
  });

  it("renders pending stage without a link", () => {
    renderWithAuth(<WorkflowTimeline stages={stages} currentStage="development" />);
    expect(screen.queryByRole("link", { name: "Testing: pending" })).not.toBeInTheDocument();
  });
});

describe("StageNavigation", () => {
  it("shows continue button for development stage", () => {
    const onContinue = vi.fn();
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={false} onContinue={onContinue} />,
    );
    expect(screen.getByRole("button", { name: "Continue to Development" })).toBeInTheDocument();
  });

  it("shows testing button text", () => {
    renderWithAuth(
      <StageNavigation nextStage="testing" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Generate Test Plan" })).toBeInTheDocument();
  });

  it("shows review button text", () => {
    renderWithAuth(
      <StageNavigation nextStage="review" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Start Review" })).toBeInTheDocument();
  });

  it("shows completed message when workflow is done", () => {
    renderWithAuth(
      <StageNavigation nextStage="completed" isSubmitting={false} onContinue={vi.fn()} />,
    );
    expect(screen.getByText(/All SDLC stages complete/)).toBeInTheDocument();
  });

  it("disables button when submitting", () => {
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={true} onContinue={vi.fn()} />,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("calls onContinue when clicked", async () => {
    const user = userEvent.setup();
    const onContinue = vi.fn();
    renderWithAuth(
      <StageNavigation nextStage="development" isSubmitting={false} onContinue={onContinue} />,
    );
    await user.click(screen.getByRole("button"));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});

describe("NewWorkflowPage", () => {
  it("renders the page heading", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(screen.getByText("New SDLC Workflow")).toBeInTheDocument();
  });

  it("renders the text input", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(screen.getByLabelText("What engineering task do you want to deliver?")).toBeInTheDocument();
  });

  it("renders example buttons", () => {
    renderWithAuth(<NewWorkflowPage />);
    expect(
      screen.getByText("Implement JWT authentication across all microservices"),
    ).toBeInTheDocument();
  });

  it("disables start button when input is empty", () => {
    renderWithAuth(<NewWorkflowPage />);
    const button = screen.getByRole("button", { name: "Start SDLC workflow" });
    expect(button).toBeDisabled();
  });

  it("enables start button when input has text", async () => {
    const user = userEvent.setup();
    renderWithAuth(<NewWorkflowPage />);
    const textarea = screen.getByLabelText("What engineering task do you want to deliver?");
    await user.type(textarea, "Test task");
    const button = screen.getByRole("button", { name: "Start SDLC workflow" });
    expect(button).toBeEnabled();
  });

  it("fills textarea when example is clicked", async () => {
    const user = userEvent.setup();
    renderWithAuth(<NewWorkflowPage />);
    const example = screen.getByText("Implement JWT authentication across all microservices");
    await user.click(example);
    const textarea = screen.getByLabelText(
      "What engineering task do you want to deliver?",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("Implement JWT authentication across all microservices");
  });
});
