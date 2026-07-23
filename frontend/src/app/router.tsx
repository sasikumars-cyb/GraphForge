import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppLayout } from "../components/layout/AppLayout";
import { RequireAuth } from "../components/layout/RequireAuth";
import { LoginPage } from "../pages/LoginPage";
import { DashboardPage } from "../pages/DashboardPage";
import { PlanningPage } from "../pages/PlanningPage";
import { DevelopmentPage } from "../pages/DevelopmentPage";
import { TestingPage } from "../pages/TestingPage";
import { ReviewPage } from "../pages/ReviewPage";
import { RunHistoryPage } from "../pages/RunHistoryPage";
import { RunDetailPage } from "../pages/RunDetailPage";
import { PullRequestsPage } from "../pages/PullRequestsPage";
import { PullRequestDetailPage } from "../pages/PullRequestDetailPage";
import { RepositoriesPage } from "../pages/RepositoriesPage";
import { RepositoryDetailPage } from "../pages/RepositoryDetailPage";
import { ArchitecturePage } from "../pages/ArchitecturePage";
import { ReportsPage } from "../pages/ReportsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { WorkflowPage, NewWorkflowPage } from "../pages/WorkflowPage";

// Exported as plain data (not just the created router) so tests can build a
// createMemoryRouter from the exact same route tree instead of duplicating
// it and risking drift.
export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { path: "/", element: <DashboardPage /> },
          { path: "/workflows/new", element: <NewWorkflowPage /> },
          { path: "/workflows/:workflowId", element: <WorkflowPage /> },
          { path: "/planning", element: <PlanningPage /> },
          { path: "/development", element: <DevelopmentPage /> },
          { path: "/testing", element: <TestingPage /> },
          { path: "/review", element: <ReviewPage /> },
          { path: "/runs", element: <RunHistoryPage /> },
          { path: "/runs/:runId", element: <RunDetailPage /> },
          { path: "/pull-requests", element: <PullRequestsPage /> },
          { path: "/pull-requests/:id", element: <PullRequestDetailPage /> },
          { path: "/repositories", element: <RepositoriesPage /> },
          { path: "/repositories/:id", element: <RepositoryDetailPage /> },
          { path: "/architecture", element: <ArchitecturePage /> },
          { path: "/reports", element: <ReportsPage /> },
          { path: "/settings", element: <SettingsPage /> },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
