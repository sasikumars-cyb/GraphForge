import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../app/auth-context";

/** Redirects to /login if there's no authenticated user; renders the
 * matched child route otherwise. Remembers where the user was headed so
 * LoginPage can send them back after a successful login. */
export function RequireAuth() {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-sm text-slate-400">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <Outlet />;
}
