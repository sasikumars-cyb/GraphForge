import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import { AppLayout } from "../components/layout/AppLayout";
import { RequireAuth } from "../components/layout/RequireAuth";
import { LoginPage } from "../pages/LoginPage";
import { OAuthCallbackPage } from "../pages/OAuthCallbackPage";
import { HomePage } from "../pages/HomePage";
import { MissionControlPage } from "../pages/MissionControlPage";
import { WorkspacePage } from "../pages/WorkspacePage";
import { PlanningPage } from "../pages/PlanningPage";
import { DevelopmentPage } from "../pages/DevelopmentPage";
import { TestingPage } from "../pages/TestingPage";
import { ReviewPage } from "../pages/ReviewPage";
import { DocumentationPage } from "../pages/DocumentationPage";
import { DocumentationHealthPage } from "../pages/DocumentationHealthPage";
import { ApiIntelligencePage } from "../pages/ApiIntelligencePage";
import { GraphParityPage } from "../pages/GraphParityPage";
import { RepositoryUnderstandingPage } from "../pages/RepositoryUnderstandingPage";
import { ImpactAnalysisPage } from "../pages/ImpactAnalysisPage";
import { DependencyQueryPage } from "../pages/DependencyQueryPage";
import { MigrationAssistantPage } from "../pages/MigrationAssistantPage";
import { RefinementPlannerPage } from "../pages/RefinementPlannerPage";
import { RefinementGraphPage } from "../pages/RefinementGraphPage";
import { RunHistoryPage } from "../pages/RunHistoryPage";
import { RunDetailPage } from "../pages/RunDetailPage";
import { PullRequestsPage } from "../pages/PullRequestsPage";
import { PullRequestDetailPage } from "../pages/PullRequestDetailPage";
import { RepositoriesPage } from "../pages/RepositoriesPage";
import { RepositoryDetailPage } from "../pages/RepositoryDetailPage";
import { ArchitecturePage } from "../pages/ArchitecturePage";
import { MetricsPage } from "../pages/MetricsPage";
import { WorkflowLLMUsagePage } from "../pages/WorkflowLLMUsagePage";
import { ReportsPage } from "../pages/ReportsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { WorkflowPage, NewWorkflowPage } from "../pages/WorkflowPage";
import { ApprovedQueuePage } from "../pages/ApprovedQueuePage";
import { NotFoundPage } from "../pages/NotFoundPage";

// Exported as plain data (not just the created router) so tests can build a
// createMemoryRouter from the exact same route tree instead of duplicating
// it and risking drift.
export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  { path: "/oauth/callback", element: <OAuthCallbackPage /> },
  // Catch-all — top-level (not nested under RequireAuth) so it renders
  // regardless of auth state for any unmatched path (typo, stale bookmark,
  // bad deep link), instead of React Router's default unstyled fallback.
  { path: "*", element: <NotFoundPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: <HomePage /> },
          { path: "/mission-control", element: <MissionControlPage /> },

          // ── Build: AI Workspace ──────────────────────────────────
          { path: "/workspace", element: <WorkspacePage /> },
          { path: "/workspace/planning", element: <PlanningPage /> },
          { path: "/workspace/development", element: <DevelopmentPage /> },
          { path: "/workspace/testing", element: <TestingPage /> },
          { path: "/workspace/pr-review", element: <ReviewPage /> },
          { path: "/workspace/documentation", element: <DocumentationPage /> },
          { path: "/workspace/documentation-health", element: <DocumentationHealthPage /> },
          { path: "/workspace/api-intelligence", element: <ApiIntelligencePage /> },
          { path: "/workspace/graph-parity", element: <GraphParityPage /> },
          {
            path: "/workspace/repository-understanding",
            element: <RepositoryUnderstandingPage />,
          },
          { path: "/workspace/impact-analysis", element: <ImpactAnalysisPage /> },
          { path: "/workspace/dependency-query", element: <DependencyQueryPage /> },
          { path: "/workspace/migration-assistant", element: <MigrationAssistantPage /> },
          { path: "/workspace/refinement-planner", element: <RefinementPlannerPage /> },
          {
            path: "/workspace/refinement-planner/graph/:conversationId",
            element: <RefinementGraphPage />,
          },

          // ── Build: Workflows ─────────────────────────────────────
          { path: "/workflows/new", element: <NewWorkflowPage /> },
          { path: "/workflows/approved", element: <ApprovedQueuePage /> },
          { path: "/workflows/:workflowId", element: <WorkflowPage /> },

          // ── Monitor ──────────────────────────────────────────────
          { path: "/runs", element: <RunHistoryPage /> },
          { path: "/runs/:runId", element: <RunDetailPage /> },
          { path: "/metrics", element: <MetricsPage /> },
          { path: "/metrics/workflows/:workflowId", element: <WorkflowLLMUsagePage /> },

          // ── Knowledge ────────────────────────────────────────────
          { path: "/repositories", element: <RepositoriesPage /> },
          { path: "/repositories/:id", element: <RepositoryDetailPage /> },
          { path: "/architecture", element: <ArchitecturePage /> },

          // ── Administration ───────────────────────────────────────
          { path: "/pull-requests", element: <PullRequestsPage /> },
          { path: "/pull-requests/:id", element: <PullRequestDetailPage /> },
          { path: "/reports", element: <ReportsPage /> },
          { path: "/settings", element: <SettingsPage /> },

          // ── Backward-compatible redirects ────────────────────────
          // Preserve existing bookmarks and deep links.
          { path: "/planning", element: <Navigate to="/workspace/planning" replace /> },
          { path: "/development", element: <Navigate to="/workspace/development" replace /> },
          { path: "/testing", element: <Navigate to="/workspace/testing" replace /> },
          { path: "/review", element: <Navigate to="/workspace/pr-review" replace /> },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
