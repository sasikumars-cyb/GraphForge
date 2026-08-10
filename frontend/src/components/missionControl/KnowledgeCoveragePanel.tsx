import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { getArchitectureSummary } from "../../lib/api/architecture";

/**
 * "How much of the engineering system does GraphForge currently
 * understand?" — real counts from GET /architecture/summary (ADR 0023's
 * "org-scale landing query"), already computed server-side, just not yet
 * surfaced on the homepage. Deliberately no fabricated "coverage %": the
 * backend has no meaningful denominator for one (100% of what — every
 * repository that could theoretically exist?), so this shows the real
 * counts themselves rather than manufacturing a ratio.
 */
export function KnowledgeCoveragePanel() {
  const { token } = useAuth();
  const query = useQuery({
    queryKey: ["architecture-summary"],
    queryFn: ({ signal }) => getArchitectureSummary(token as string, signal),
    enabled: token !== null,
  });

  if (query.isPending) {
    return (
      <section aria-label="Knowledge coverage" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Knowledge coverage</h2>
        <div className="h-32 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  const summary = query.data;
  if (!summary) {
    return (
      <section aria-label="Knowledge coverage" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Knowledge coverage</h2>
        <p className="rounded-xl border border-line-muted bg-surface px-4 py-3 text-xs text-fg-muted">
          Unavailable right now.
        </p>
      </section>
    );
  }

  const indexed = summary.total_repositories - summary.unindexed_count;

  return (
    <section aria-label="Knowledge coverage" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold text-fg">Knowledge coverage</h2>
      <Link
        to="/architecture"
        className="grid grid-cols-2 gap-x-4 gap-y-2.5 rounded-xl border border-line-muted bg-surface px-4 py-3.5 transition-colors hover:bg-surface-hover sm:grid-cols-3"
      >
        <CoverageStat label="Indexed" value={indexed} tone="success" />
        <CoverageStat label="Stale" value={summary.stale_count} tone={summary.stale_count > 0 ? "warning" : "neutral"} />
        <CoverageStat label="Unindexed" value={summary.unindexed_count} tone={summary.unindexed_count > 0 ? "warning" : "neutral"} />
        <CoverageStat label="Components" value={summary.total_nodes} />
        <CoverageStat
          label="Cross-repo relationships"
          value={summary.total_cross_repository_edges}
          className="col-span-2 sm:col-span-1"
        />
      </Link>
    </section>
  );
}

function CoverageStat({
  label,
  value,
  tone = "neutral",
  className = "",
}: {
  label: string;
  value: number;
  tone?: "success" | "warning" | "neutral";
  className?: string;
}) {
  const valueColor = {
    success: "text-success-fg",
    warning: "text-warning-fg",
    neutral: "text-fg",
  }[tone];

  return (
    <div className={className}>
      <p className={`text-lg font-semibold tabular-nums ${valueColor}`}>
        {value.toLocaleString()}
      </p>
      <p className="text-xs text-fg-muted">{label}</p>
    </div>
  );
}
