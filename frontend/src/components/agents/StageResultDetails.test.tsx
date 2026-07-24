import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  DevelopmentResultDetails,
  PlanningResultDetails,
  TestingResultDetails,
} from "./StageResultDetails";
import type { DevelopmentPlanResult, PlanningResult, TestPlanResult } from "../../types/agent";

describe("PlanningResultDetails", () => {
  it("renders implementation steps, affected components, and risk considerations", () => {
    const result: PlanningResult = {
      executive_summary: "A plan.",
      implementation_steps: [
        { order: 1, description: "Add a filter", affected_component: "AuthFilter", risk_note: "" },
      ],
      affected_components: ["AuthService"],
      kafka_topics_involved: [],
      risk_considerations: ["Token expiry must be handled"],
      graph_context_used: true,
      repositories_consulted: ["order-service"],
    };
    render(<PlanningResultDetails result={result} />);
    expect(screen.getByText("Add a filter")).toBeInTheDocument();
    expect(screen.getByText("AuthService")).toBeInTheDocument();
    expect(screen.getByText("Token expiry must be handled")).toBeInTheDocument();
    expect(screen.getByText("order-service")).toBeInTheDocument();
  });
});

describe("DevelopmentResultDetails", () => {
  it("renders phases, repositories with their reason, and components with their change_description", () => {
    const result: DevelopmentPlanResult = {
      goal: "x",
      executive_summary: "A blueprint.",
      repositories: [{ name: "payment-service", owner: "acme", reason: "Needs retry logic" }],
      components: [
        {
          name: "PaymentController",
          component_type: "Controller",
          repository: "payment-service",
          file_path: "src/PaymentController.java",
          change_description: "Add retry annotation",
        },
      ],
      dependencies: [],
      reusable_implementations: [],
      implementation_phases: [
        {
          order: 1,
          title: "Add retries",
          description: "Wrap calls",
          affected_components: [],
          estimated_complexity: "low",
          depends_on_phases: [],
        },
      ],
      risks: [],
      recommendations: [],
      graph_context_used: true,
      repositories_consulted: [],
    };
    render(<DevelopmentResultDetails result={result} />);
    expect(screen.getByText("Needs retry logic")).toBeInTheDocument();
    expect(screen.getByText("Add retry annotation")).toBeInTheDocument();
    expect(screen.getByText("Wrap calls")).toBeInTheDocument();
    expect(
      screen.getByText(/no repositories, branches, or commits are created/),
    ).toBeInTheDocument();
  });
});

describe("TestingResultDetails", () => {
  it("renders regression tests, integration tests, and edge cases", () => {
    const result: TestPlanResult = {
      goal: "x",
      executive_summary: "A strategy.",
      test_scope: { in_scope: [], out_of_scope: [] },
      affected_repositories: [],
      affected_components: [],
      regression_tests: [
        {
          component: "OrderService",
          description: "Order totals stay correct",
          priority: "high",
          automated: true,
        },
      ],
      integration_tests: [
        {
          source_component: "OrderService",
          target_component: "PaymentService",
          relationship: "CALLS",
          description: "Order pays correctly",
          priority: "high",
        },
      ],
      edge_cases: [
        {
          description: "Empty cart checkout",
          component: "OrderService",
          severity: "medium",
          category: "boundary",
        },
      ],
      environment_requirements: [],
      execution_order: [],
      automation_candidates: [],
      manual_validations: [],
      risks: [],
      recommendations: [],
      graph_context_used: true,
      repositories_consulted: [],
    };
    render(<TestingResultDetails result={result} />);
    expect(screen.getByText("Order totals stay correct")).toBeInTheDocument();
    expect(screen.getByText("Order pays correctly")).toBeInTheDocument();
    expect(screen.getByText("Empty cart checkout")).toBeInTheDocument();
  });
});
