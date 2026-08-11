import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import { getReviewReportHtml } from "../../lib/api/analysis";

/**
 * PR Review's "open the executive dashboard" action — the one visual
 * artifact this agent produces that isn't a `step.result.blueprint`
 * diagram (see StageResultPanel's own docstring for that family), so it
 * needs its own button rather than falling under the generic "Visual
 * Blueprint" tab.
 *
 * Originally lived only inline in ReviewPage.tsx (the page shown right
 * after submitting a PR review), which meant the exact same completed
 * run, reached instead via Run History -> RunDetailPage, had no way to
 * open it — "the visual option is no more" once you left the submission
 * page. Both pages now render this one component so there's a single
 * place this ever breaks, not two copies that can drift.
 */
export function ViewVisualReportButton({ pullRequestId }: { pullRequestId: string | null }) {
  const { token } = useAuth();
  const [isOpening, setIsOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pullRequestId) return null;

  async function handleOpenReport() {
    if (!token || !pullRequestId) return;
    const reportWindow = window.open("", "_blank");
    setIsOpening(true);
    setError(null);
    try {
      const html = await getReviewReportHtml(token, pullRequestId);
      const blobUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      if (reportWindow) {
        reportWindow.location.href = blobUrl;
      } else {
        setError("Pop-up blocked - allow pop-ups for this site to view the report.");
      }
    } catch (err) {
      reportWindow?.close();
      setError(err instanceof Error ? err.message : "Failed to load the visual report.");
    } finally {
      setIsOpening(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={() => void handleOpenReport()}
        disabled={isOpening}
        title="Opens the full executive dashboard - score bars, filterable findings, per-file review cards"
        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
        {isOpening ? "Opening…" : "View Visual Report"}
      </button>
      {error && <p className="text-xs text-danger-fg">{error}</p>}
    </div>
  );
}

/** `run.subject.subject_id` is "pr:<uuid>" for pull_request subjects (see
 * resolve_pr_subject in app/agents/review_adapter.py) — the visual report
 * endpoint keys off that same pull request id. Shared so both call sites
 * agree on the convention rather than re-deriving it independently. */
export function pullRequestIdFromSubject(goal: string, subjectId: string): string | null {
  return goal === "review_pr" && subjectId.startsWith("pr:") ? subjectId.slice(3) : null;
}
