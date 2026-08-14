import { GitBranch } from "lucide-react";
import type { HeaderVM } from "../../lib/api/reports";
import { ReadinessBadge } from "./badges";

/** [ Investigation Question ] — the top of the report's visual hierarchy
 * (ADR 0024 §3/§4). Answers "what was GraphForge trying to determine" in
 * the first thing a reader sees, before any judgment or detail.
 *
 * `question` is the user's own request (Context Discovery's
 * `original_request`, falling back to the workflow's `original_prompt`) —
 * so it leads, at heading size. `workflow_title` is an AI-generated
 * 5-10 word label for that same request plus the workflow it ran through:
 * useful provenance, but it isn't what the report is *about*, and having
 * it as the headline made every report read as a status update on a
 * workflow rather than an answer to a question. */
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
      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-fg-subtle">
        You asked
      </p>
      <h1 className="mt-1 whitespace-pre-line font-display text-xl font-semibold leading-snug tracking-tight text-fg">
        {header.question}
      </h1>
      <p className="mt-2 text-sm text-fg-muted">
        Investigated as <span className="text-fg-secondary">{header.workflow_title}</span>
      </p>
    </div>
  );
}
