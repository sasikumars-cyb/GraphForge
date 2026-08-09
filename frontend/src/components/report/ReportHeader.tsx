import { GitBranch } from "lucide-react";
import type { HeaderVM } from "../../lib/api/reports";
import { ReadinessBadge } from "./badges";

/** [ Investigation Question ] — the top of the report's visual hierarchy
 * (ADR 0024 §3/§4). Answers "what was GraphForge trying to determine" in
 * the first thing a reader sees, before any judgment or detail. */
export function ReportHeader({ header }: { header: HeaderVM }) {
  return (
    <div className="rounded-xl border border-line-muted bg-surface p-5">
      <div className="flex flex-wrap items-center gap-2">
        <ReadinessBadge readiness={header.readiness} />
        {header.repository && (
          <span className="inline-flex items-center gap-1 rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-fg-muted">
            <GitBranch className="h-3 w-3" aria-hidden="true" />
            {header.repository}
          </span>
        )}
      </div>
      <h1 className="mt-3 font-display text-xl font-semibold tracking-tight text-fg">
        {header.workflow_title}
      </h1>
      <p className="mt-1.5 text-sm leading-relaxed text-fg-muted">{header.question}</p>
    </div>
  );
}
