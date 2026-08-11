import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ShieldCheck, GitBranch } from "lucide-react";
import { useAuth } from "../app/auth-context";
import { ApiError, API_BASE_URL } from "../lib/api/client";

// There's no self-serve sign-up page today — account creation really is
// API-only — so this can't link to an in-app flow that doesn't exist. What
// it shouldn't do is hand a first-time visitor a raw HTTP verb and path
// (`POST /api/v1/auth/register`) as the primary reading, before they've
// even signed in once. `/docs` (FastAPI's interactive Swagger UI) is a
// real, working link for whoever this message is actually for — derived
// from the same API_BASE_URL every request already uses, not hardcoded to
// one port, so it keeps working if that changes.
const API_DOCS_URL = `${API_BASE_URL.replace(/\/api\/v1\/?$/, "")}/docs`;

export function LoginPage() {
  const { user, isLoading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Already logged in and just landed on /login directly: bounce onward.
  if (!isLoading && user) {
    const from = (location.state as { from?: Location })?.from?.pathname ?? "/";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      const from = (location.state as { from?: Location })?.from?.pathname ?? "/";
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-4 text-fg">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2 text-center">
          <ShieldCheck className="h-8 w-8 text-accent-fg" aria-hidden="true" />
          <h1 className="text-xl font-semibold">Sign in to GraphForge</h1>
          <p className="text-sm text-fg-muted">Review pull request risk before it ships.</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 rounded-xl border border-line-muted bg-surface p-6 shadow-sm"
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-accent-line"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-accent-line"
            />
          </label>

          {error && (
            <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 rounded-md bg-accent-solid px-3 py-2 text-sm font-semibold text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting ? "Signing in…" : "Sign in"}
          </button>

          <div className="flex items-center gap-3 text-xs text-fg-muted">
            <span className="h-px flex-1 bg-surface-raised" />
            or
            <span className="h-px flex-1 bg-surface-raised" />
          </div>

          {/* KAN-34 — a real top-level navigation (not a `login`-style
              fetch): GitHub's authorize page can't be reached via XHR, and
              there's no token to obtain client-side until GitHub redirects
              back through /api/v1/auth/github/callback -> /oauth/callback
              (see OAuthCallbackPage). */}
          <a
            href={`${API_BASE_URL}/auth/github/login`}
            className="flex items-center justify-center gap-2 rounded-md border border-line px-3 py-2 text-sm font-medium text-fg-secondary transition-colors hover:border-line-strong hover:bg-surface-hover"
          >
            <GitBranch className="h-4 w-4" aria-hidden="true" />
            Continue with GitHub
          </a>
        </form>

        <p className="mt-4 text-center text-xs text-fg-muted">
          No account yet? Ask your workspace admin to create one, or see the{" "}
          <a
            href={API_DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-fg-secondary"
          >
            API docs
          </a>{" "}
          for self-serve registration.
        </p>
      </div>
    </main>
  );
}
