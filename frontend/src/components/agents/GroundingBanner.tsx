import { Link } from "react-router-dom";
import { Link2, Layers, CircleSlash } from "lucide-react";

interface GroundingBannerProps {
  /** The agent's own report of whether it read the architecture graph. */
  graphContextUsed: boolean;
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
 * Three states, not two. A run with no graph is not automatically a problem:
 * a greenfield project legitimately has no repository to index, and
 * PlanningPage already branches on exactly this condition to show
 * GreenfieldRecommendations. So "no graph AND no repositories" is reported
 * neutrally with an offer, while "repositories read but no graph" is the
 * genuinely degraded middle case and is the one flagged amber.
 */
export function GroundingBanner({
  graphContextUsed,
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

  if (graphContextUsed) {
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

  if (repos.length > 0) {
    return (
      <Banner
        icon={Layers}
        tone="warning"
        heading="Partially grounded"
        body={`Repositories were consulted, but no architecture graph was available — so this ${subject} could not use dependency or call-path information.`}
        facts={facts}
        repos={repos}
        action={{ to: "/repositories", label: "Index a repository" }}
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
        {repos.length > 0 && (
          <p className={`mt-1 truncate font-mono text-[11px] ${styles.body}`} title={repos.join(", ")}>
            {repos.join(" · ")}
          </p>
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
