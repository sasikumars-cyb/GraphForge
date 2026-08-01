import { createBrowserRouter, Navigate, type RouteObject } from "react-router-dom";
import { AppLayout } from "../components/layout/AppLayout";
import { RequireAuth } from "../components/layout/RequireAuth";
import { LoginPage } from "../pages/LoginPage";
import { ControlCenterPage } from "../pages/ControlCenterPage";
import { WorkspacePage } from "../pages/WorkspacePage";
import { PlanningPage } from "../pages/PlanningPage";
import { DevelopmentPage } from "../pages/DevelopmentPage";
import { TestingPage } from "../pages/TestingPage";
import { ReviewPage } from "../pages/ReviewPage";
import { DocumentationPage } from "../pages/DocumentationPage";
import { DocumentationHealthPage } from "../pages/DocumentationHealthPage";
import { RunHistoryPage } from "../pages/RunHistoryPage";
import { RunDetailPage } from "../pages/RunDetailPage";
import { PullRequestsPage } from "../pages/PullRequestsPage";
import { PullRequestDetailPage } from "../pages/PullRequestDetailPage";
import { RepositoriesPage } from "../pages/RepositoriesPage";
import { RepositoryDetailPage } from "../pages/RepositoryDetailPage";
import { ArchitecturePage } from "../pages/ArchitecturePage";
import { MetricsPage } from "../pages/MetricsPage";
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
          { path: "/", element: <ControlCenterPage /> },

          // ── Build: AI Workspace ──────────────────────────────────
          { path: "/workspace", element: <WorkspacePage /> },
          { path: "/workspace/planning", element: <PlanningPage /> },
          { path: "/workspace/development", element: <DevelopmentPage /> },
          { path: "/workspace/testing", element: <TestingPage /> },
          { path: "/workspace/pr-review", element: <ReviewPage /> },
          { path: "/workspace/documentation", element: <DocumentationPage /> },
          { path: "/workspace/documentation-health", element: <DocumentationHealthPage /> },

          // ── Build: Workflows ─────────────────────────────────────
          { path: "/workflows/new", element: <NewWorkflowPage /> },
          { path: "/workflows/approved", element: <ApprovedQueuePage /> },
          { path: "/workflows/:workflowId", element: <WorkflowPage /> },

          // ── Monitor ──────────────────────────────────────────────
          { path: "/runs", element: <RunHistoryPage /> },
          { path: "/runs/:runId", element: <RunDetailPage /> },
          { path: "/metrics", element: <MetricsPage /> },

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
