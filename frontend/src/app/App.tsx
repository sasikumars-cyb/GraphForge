import { RouterProvider } from "react-router-dom";
import { AiModelProvider } from "./AiModelContext";
import { AuthProvider } from "./AuthContext";
import { ErrorBoundary } from "../components/layout/ErrorBoundary";
import { ThemeProvider } from "../theme/ThemeProvider";
import { router } from "./router";

export function App() {
  return (
    <ErrorBoundary>
      {/* Outermost so the login page (outside AuthProvider's authenticated
          routes) is themed too. */}
      <ThemeProvider>
        <AuthProvider>
          <AiModelProvider>
            <RouterProvider router={router} />
          </AiModelProvider>
        </AuthProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}
