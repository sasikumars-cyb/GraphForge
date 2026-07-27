import { RouterProvider } from "react-router-dom";
import { AiModelProvider } from "./AiModelContext";
import { AuthProvider } from "./AuthContext";
import { ErrorBoundary } from "../components/layout/ErrorBoundary";
import { router } from "./router";

export function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <AiModelProvider>
          <RouterProvider router={router} />
        </AiModelProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
}
