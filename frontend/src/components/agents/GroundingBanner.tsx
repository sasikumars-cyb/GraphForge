import { Link } from "react-router-dom";
import { AlertTriangle, Link2, CircleSlash } from "lucide-react";
import type { GroundingStatus } from "../../types/agent";

interface GroundingBannerProps {
  /** The agent's own report of whether it read the architecture graph —
   * still used for the success case's exact styling, but `groundingStatus`
   * (below) is the actual discriminant for which of the three banners
   * renders; see that field's own docstring for why. */
  graphContextUsed: boolean;
  /** UX audit P1.3/P1.4: the backend's own classification of *why* this
   * run isn't grounded (or is) — see app.agents.verification.
   * grounding_status. Optional only so old persisted results (from before
   * this field existed) fall back to the previous, less precise two-signal
   * heuristic rather than crashing; every run generated from now on sets
   * it. Do not add a fourth way to guess this state — this prop plus
   * `graphContextUsed`/`repositoriesConsulted` (kept only for the
   * fallback) are the complete input. */
  groundingStatus?: GroundingStatus;
  repositoriesConsulted?: string[];
  /** Evidence items on the step, when the caller has them. */
  evidenceCount?: number;
  /** What was produced — "plan", "blueprint", "test plan". Used in prose. */
  subject: string;
}

/**
 * Whether this AI output came from the user's actual codebase or from the
 * model's general knowledge — stated up front, at full weight.
 *
 * This replaces a 12px muted line at the *bottom* of each result ("Graph
 * context: Used architecture graph data"). That is the single fact which
 * separates GraphForge from pasting a ticket into a chatbot, and it was
 * rendered in the visual weight class of a copyright notice, below the fold
 * of a long card.
 *
 * Three states, not two — and, since the UX audit's P1.4 fix, driven by one
 * backend-computed classification (`groundingStatus`) instead of this
 * component re-deriving its own guess from `graphContextUsed`/`repos.
 * length`. That old heuristic could not distinguish "the graph service
 * itself was unreachable" (genuinely worth a retry) from "nothing is
 * indexed yet" (genuinely a new/unindexed project, expected) — both
 * collapsed into the same "No codebase context" copy, which is how a real
 * infrastructure failure ended up telling a user this was "expected for a
 * new project." Each state now has its own accurate heading, explanation,
 * and — critically — its own correct next action (Retry vs. Index a
 * repository vs. no action needed).
 */
export function GroundingBanner({
  graphContextUsed,
  groundingStatus,
  repositoriesConsulted,
  evidenceCount,
  subject,
}: GroundingBannerProps) {
  const repos = repositoriesConsulted ?? [];
  const facts = [
    repos.length > 0 ? `${repos.length} repositor${repos.length === 1 ? "y" : "ies"}` : null,
    evidenceCount !== undefined && evidenceCount > 0
      ? `${evidenceCount} evidence item${evidenceCount === 1 ? "" : "s"}`
      : null,
  ].filter(Boolean) as string[];

  // Fallback for results persisted before grounding_status existed: the
  // same two-signal guess this component used to make unconditionally.
  const status: GroundingStatus =
    groundingStatus ?? (graphContextUsed ? "grounded" : repos.length > 0 ? "unavailable" : "not_indexed");

  if (status === "grounded") {
    return (
      <Banner
        icon={Link2}
        tone="success"
        heading="Grounded in your architecture"
        body={`This ${subject} was built from your indexed architecture graph.`}
        facts={facts}
        repos={repos}
      />
    );
  }

  if (status === "unavailable") {
    return (
      <Banner
        icon={AlertTriangle}
        tone="warning"
        heading="Architecture graph unavailable"
        body={`We could not retrieve indexed architecture data for this request, so this ${subject} uses first-principles reasoning instead of repository-grounded evidence. This is an infrastructure issue, not an indexing gap — retrying may succeed once the graph service recovers.`}
        facts={facts}
        repos={repos}
      />
    );
  }

  return (
    <Banner
      icon={CircleSlash}
      tone="neutral"
      heading="No codebase context"
      body={`This ${subject} reflects general engineering practice, not your codebase. That is expected for a new project; for work on existing services, index the repository first.`}
      facts={facts}
      repos={repos}
      action={{ to: "/repositories", label: "Index a repository" }}
    />
  );
}

const TONE_STYLES = {
  success: {
    wrap: "border-success-line/30 bg-success-bg",
    heading: "text-success-fg",
    body: "text-success-fg/80",
    icon: "text-success-fg",
  },
  warning: {
    wrap: "border-warning-line/30 bg-warning-bg",
    heading: "text-warning-fg",
    body: "text-warning-fg/80",
    icon: "text-warning-fg",
  },
  neutral: {
    wrap: "border-line-muted bg-surface",
    heading: "text-fg-secondary",
    body: "text-fg-muted",
    icon: "text-fg-muted",
  },
} as const;

function Banner({
  icon: Icon,
  tone,
  heading,
  body,
  facts,
  repos,
  action,
}: {
  icon: typeof Link2;
  tone: keyof typeof TONE_STYLES;
  heading: string;
  body: string;
  facts: string[];
  repos: string[];
  action?: { to: string; label: string };
}) {
  const styles = TONE_STYLES[tone];
  return (
    <div className={`flex items-start gap-3 rounded-xl border px-4 py-3 ${styles.wrap}`}>
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${styles.icon}`} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <p className={`text-sm font-semibold ${styles.heading}`}>{heading}</p>
          {facts.length > 0 && (
            <p className={`text-xs ${styles.body}`}>{facts.join(" · ")}</p>
          )}
        </div>
        <p className={`mt-0.5 text-xs ${styles.body}`}>{body}</p>
        {/* UX audit P1.2: this is the one canonical place the full
            grounding-scope repo list lives — collapsed by default (the
            names rarely matter, the count already does via `facts`
            above) rather than always-on, truncated, ellipsized text. */}
        {repos.length > 0 && (
          <details className="mt-1">
            <summary
              className={`w-fit cursor-pointer text-[11px] font-medium ${styles.body} hover:underline`}
            >
              Show repositories
            </summary>
            <p className={`mt-1 font-mono text-[11px] ${styles.body}`}>{repos.join(" · ")}</p>
          </details>
        )}
      </div>
      {action && (
        <Link
          to={action.to}
          className="focus-ring shrink-0 rounded-md px-2.5 py-1 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover"
        >
          {action.label}
        </Link>
      )}
    </div>
  );
}
