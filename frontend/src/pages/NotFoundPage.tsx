import { Link } from "react-router-dom";
import { Compass } from "lucide-react";

/** Catch-all for any unmatched route — a typo'd URL, a stale bookmark, or a
 * bad deep link previously fell through to React Router's default,
 * unstyled "no routes matched" page instead of the app's own layout. */
export function NotFoundPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 px-6">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <div className="rounded-full bg-slate-800/80 p-3 ring-1 ring-inset ring-slate-700">
          <Compass className="h-6 w-6 text-slate-400" aria-hidden="true" />
        </div>
        <h1 className="text-lg font-semibold text-slate-100">Page not found</h1>
        <p className="text-sm text-slate-400">
          The page you're looking for doesn't exist, or the link may be out of date.
        </p>
        <Link
          to="/"
          className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-400"
        >
          Back to dashboard
        </Link>
      </div>
    </div>
  );
}
