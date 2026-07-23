import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ShieldCheck, GitBranch } from "lucide-react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";

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
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 text-slate-100">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2 text-center">
          <ShieldCheck className="h-8 w-8 text-sky-400" aria-hidden="true" />
          <h1 className="text-xl font-semibold">Sign in to GraphForge</h1>
          <p className="text-sm text-slate-400">Review pull request risk before it ships.</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20"
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
            />
          </label>

          {error && (
            <p role="alert" className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={isSubmitting}
            className="mt-1 rounded-md bg-sky-500 px-3 py-2 text-sm font-semibold text-slate-950 transition-colors hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-sky-500/50"
          >
            {isSubmitting ? "Signing in…" : "Sign in"}
          </button>

          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span className="h-px flex-1 bg-slate-800" />
            or
            <span className="h-px flex-1 bg-slate-800" />
          </div>

          <button
            type="button"
            disabled
            title="GitHub OAuth is not implemented yet"
            className="flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-medium text-slate-400 disabled:cursor-not-allowed"
          >
            <GitBranch className="h-4 w-4" aria-hidden="true" />
            Continue with GitHub (coming soon)
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-slate-500">
          No account yet? Create one at{" "}
          <code className="rounded bg-slate-900 px-1 py-0.5">POST /api/v1/auth/register</code> — see
          the API docs at <code className="rounded bg-slate-900 px-1 py-0.5">/docs</code>.
        </p>
      </div>
    </main>
  );
}
