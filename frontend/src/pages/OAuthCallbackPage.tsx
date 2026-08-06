import { useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "../app/auth-context";

// Mirrors the backend's AppError.error_code for every failure
// `github_login_service.handle_login_callback` can raise (see
// backend/app/api/v1/routers/oauth.py) - not every AppError subclass in
// the app, just the ones this specific flow's callback actually redirects
// here with. Unrecognized codes (a future backend error type this page
// hasn't been told about yet) fall back to a generic message rather than
// showing a raw error code to the user.
const ERROR_MESSAGES: Record<string, string> = {
  github_login_not_configured: "GitHub sign-in isn't configured for this workspace yet.",
  github_account_is_local:
    "An account with this email already exists — log in with your password instead.",
  github_email_unavailable:
    "GitHub didn't share a verified email address. Grant email access and try again, or use email/password login.",
  unauthorized: "That sign-in link has expired or was already used — try again.",
  github_login_failed: "Something went wrong signing in with GitHub. Please try again.",
};

/**
 * KAN-34 — lands here after `/api/v1/auth/github/callback` redirects the
 * browser back from GitHub, carrying either `?token=` (success — this app's
 * own JWT, same shape `login()` already stores) or `?error=` (an
 * `AppError.error_code`). Not a route any link points at directly.
 */
export function OAuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const { loginWithToken } = useAuth();
  const [adopted, setAdopted] = useState(false);

  const token = searchParams.get("token");
  const errorCode = searchParams.get("error");

  useEffect(() => {
    if (token) {
      loginWithToken(token);
      setAdopted(true);
    }
    // `loginWithToken` is stable across renders (see AuthProvider) - only
    // re-run if the token in the URL itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (token && adopted) {
    return <Navigate to="/" replace />;
  }

  if (errorCode) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas px-4 text-fg">
        <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
          <ShieldAlert className="h-8 w-8 text-danger-fg" aria-hidden="true" />
          <h1 className="text-lg font-semibold">Couldn't sign in with GitHub</h1>
          <p className="text-sm text-fg-muted">
            {ERROR_MESSAGES[errorCode] ?? ERROR_MESSAGES.github_login_failed}
          </p>
          <a
            href="/login"
            className="rounded-md border border-line px-3 py-2 text-sm font-medium text-fg-secondary hover:border-line-strong"
          >
            Back to sign in
          </a>
        </div>
      </main>
    );
  }

  // Neither `token` nor `error` present (direct navigation, not an actual
  // OAuth redirect) - nothing to adopt, nothing to explain.
  return <Navigate to="/login" replace />;
}
