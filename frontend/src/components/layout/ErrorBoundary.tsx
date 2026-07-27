import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Top-level render-error containment. Without this, an unhandled exception
 * anywhere in the tree (a null/malformed API response shape a component
 * didn't guard against, a bad cast, ...) unmounts the entire app to a blank
 * white screen with no way to recover short of a manual reload — and no
 * indication to the user that anything went wrong at all.
 *
 * React error boundaries must be class components — there is no Hooks
 * equivalent for componentDidCatch/getDerivedStateFromError.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Server-side/monitoring visibility — this is exactly the failure mode
    // that otherwise silently white-screens with nothing in the console
    // beyond React's own dev-only warning.
    console.error("Unhandled render error caught by ErrorBoundary:", error, info.componentStack);
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-slate-950 px-6">
          <div className="flex max-w-md flex-col items-center gap-4 text-center">
            <div className="rounded-full bg-rose-500/10 p-3 ring-1 ring-inset ring-rose-500/30">
              <AlertTriangle className="h-6 w-6 text-rose-400" aria-hidden="true" />
            </div>
            <h1 className="text-lg font-semibold text-slate-100">Something went wrong</h1>
            <p className="text-sm text-slate-400">
              An unexpected error occurred while rendering this page. Reloading usually fixes it —
              nothing you were working on was lost on the server.
            </p>
            <button
              type="button"
              onClick={this.handleReload}
              className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-400"
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
