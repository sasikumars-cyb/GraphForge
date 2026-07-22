import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppLayout } from "../components/layout/AppLayout";
import { RequireAuth } from "../components/layout/RequireAuth";
import { LoginPage } from "../pages/LoginPage";
import { DashboardPage } from "../pages/DashboardPage";
import { PullRequestsPage } from "../pages/PullRequestsPage";
import { RepositoriesPage } from "../pages/RepositoriesPage";
import { ArchitecturePage } from "../pages/ArchitecturePage";
import { ReportsPage } from "../pages/ReportsPage";
import { SettingsPage } from "../pages/SettingsPage";

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
          { path: "/pull-requests", element: <PullRequestsPage /> },
          { path: "/repositories", element: <RepositoriesPage /> },
          { path: "/architecture", element: <ArchitecturePage /> },
          { path: "/reports", element: <ReportsPage /> },
          { path: "/settings", element: <SettingsPage /> },
        ],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);
