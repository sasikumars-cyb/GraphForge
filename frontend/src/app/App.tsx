import { RouterProvider } from "react-router-dom";
import { AiModelProvider } from "./AiModelContext";
import { AuthProvider } from "./AuthContext";
import { router } from "./router";

export function App() {
  return (
    <AuthProvider>
      <AiModelProvider>
        <RouterProvider router={router} />
      </AiModelProvider>
    </AuthProvider>
  );
}
