import { useState } from "react";
import { AlertTriangle, CheckCircle2, GitBranch, ListTodo, PenLine, XCircle, X } from "lucide-react";
import { Card } from "../Card";
import type { ContextDiscoveryResult, DiscoverySummaryItem } from "../../types/agent";
import { overrideStageResult } from "../../lib/api/workflows";
import { useAuth } from "../../app/auth-context";

const SUMMARY_ICON: Record<DiscoverySummaryItem["status"], typeof CheckCircle2> = {
  ok: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
};

const SUMMARY_COLOR: Record<DiscoverySummaryItem["status"], string> = {
  ok: "text-success-fg",
  warning: "text-warning-fg",
  error: "text-danger-fg",
};

/** The human-facing report generated *from* WorkingContext on the backend
 * (see reasoning_loop.build_discovery_summary) — downstream agents consume
 * the structured result; this is what a human reviewing Context Discovery
 * actually reads instead of the raw repository/component lists. */
function DiscoverySummarySection({ result }: { result: ContextDiscoveryResult }) {
  const summary = result.discovery_summary;
  if (!summary || summary.items.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-fg-secondary">Discovery Summary</p>
        <span className="text-xs font-medium text-fg-muted">Readiness: {summary.readiness}</span>
      </div>
      <ul className="flex flex-col gap-1">
        {summary.items.map((item, i) => {
          const Icon = SUMMARY_ICON[item.status];
          return (
            <li key={i} className={`flex items-start gap-1.5 text-xs ${SUMMARY_COLOR[item.status]}`}>
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{item.label}</span>
            </li>
          );
        })}
      </ul>
      {summary.headline && <p className="text-xs text-fg-muted">{summary.headline}</p>}
    </div>
  );
}

interface ContextExplorerPanelProps {
  workflowId: string;
  result: ContextDiscoveryResult;
  /** Called after a correction is saved so the caller can refresh the
   * workflow (the next stage reads the corrected value via
   * get_stage_result() as soon as this commits — no other state changes). */
  onOverridden: () => void;
}

/** The review UI for Context Discovery's output (see the Context Discovery /
 * Context Explorer architecture review) — presented during the existing
 * approval transition between the context_discovery and planning stages,
 * not a workflow stage itself. Reuses the same approval flow the
 * ApprovalGateBanner already renders alongside; this only adds visibility
 * into *what* was discovered and a small correction affordance before the
 * human approves moving on to Planning.
 *
 * The edit affordance is deliberately narrow: a human corrects the
 * `graph_context_text` blob (the actual text Planning's prompt is built
 * from) rather than editing the structured repository/component lists
 * individually — a full structured editor is more machinery than a "small
 * override mechanism" calls for, and graph_context_text is the one field
 * that actually reaches the next stage's prompt (see
 * app.agents.planning.agent._resolve_context on the backend). */
export function ContextExplorerPanel({
  workflowId,
  result,
  onOverridden,
}: ContextExplorerPanelProps) {
  const { token } = useAuth();
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(result.graph_context_text);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    if (!token) return;
    setIsSaving(true);
    setError(null);
    try {
      await overrideStageResult(token, workflowId, "context_discovery", {
        override: { graph_context_text: draft },
      });
      setIsEditing(false);
      onOverridden();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save correction.");
    } finally {
      setIsSaving(false);
    }
  };

  const references = result.resolved_references;
  const repoCount = result.indexed_repositories.length;
  const componentCount = result.graph_components.length;
  const topicCount = result.graph_topics.length;

  return (
    <Card
      title="Context Explorer"
      description="What Context Discovery found before Planning runs — review and correct it if needed."
      action={
        !isEditing && (
          <button
            type="button"
            onClick={() => {
              setDraft(result.graph_context_text);
              setIsEditing(true);
            }}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
          >
            <PenLine className="h-3.5 w-3.5" aria-hidden="true" />
            Correct
          </button>
        )
      }
    >
      <div className="flex flex-col gap-4">
        <DiscoverySummarySection result={result} />

        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <p className="text-xs text-fg-muted">Indexed repositories</p>
            <p className="text-lg font-semibold text-fg">{repoCount}</p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <p className="text-xs text-fg-muted">Components</p>
            <p className="text-lg font-semibold text-fg">{componentCount}</p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <p className="text-xs text-fg-muted">Kafka topics</p>
            <p className="text-lg font-semibold text-fg">{topicCount}</p>
          </div>
        </div>

        {repoCount > 0 && (
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-fg-secondary">
              <GitBranch className="h-3.5 w-3.5" aria-hidden="true" />
              Indexed repositories
            </p>
            <div className="flex flex-wrap gap-1.5">
              {result.indexed_repositories.map((repo, i) => (
                <span
                  key={i}
                  className="rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-fg-secondary ring-1 ring-inset ring-line-muted"
                >
                  {String(repo.name ?? "unknown")}
                </span>
              ))}
            </div>
          </div>
        )}

        {references.length > 0 && (
          <div>
            <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-fg-secondary">
              <ListTodo className="h-3.5 w-3.5" aria-hidden="true" />
              Resolved references
            </p>
            <ul className="flex flex-col gap-1">
              {references.map((ref, i) => (
                <li key={i} className="text-xs text-fg-muted">
                  <span className="font-medium text-fg-secondary">{ref.type}</span>:{" "}
                  {ref.normalized_value}
                </li>
              ))}
            </ul>
          </div>
        )}

        {result.additional_context_recommendation?.should_search && (
          <div className="rounded-lg border border-warning-line/30 bg-warning-bg px-3 py-2 text-xs text-warning-fg">
            {result.additional_context_recommendation.reasoning}
          </div>
        )}

        {isEditing ? (
          <div className="flex flex-col gap-2">
            <label htmlFor="context-correction" className="text-xs font-medium text-fg-secondary">
              Graph context passed to Planning
            </label>
            <textarea
              id="context-correction"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={isSaving}
              rows={8}
              className="w-full rounded-lg border border-line bg-surface-raised px-3 py-2 font-mono text-xs text-fg placeholder-fg-subtle focus:border-accent-line disabled:opacity-50"
            />
            {error && <p className="text-xs text-danger-fg">{error}</p>}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent-solid px-3 py-1.5 text-xs font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSaving ? "Saving…" : "Save correction"}
              </button>
              <button
                type="button"
                onClick={() => setIsEditing(false)}
                disabled={isSaving}
                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line hover:bg-surface-raised"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <details className="group">
            <summary className="cursor-pointer text-xs font-medium text-fg-muted hover:text-fg-secondary">
              View graph context passed to Planning
            </summary>
            <p className="mt-2 rounded-lg bg-canvas p-3 font-mono text-xs whitespace-pre-wrap text-fg-secondary">
              {result.graph_context_text || "No graph context text."}
            </p>
          </details>
        )}
      </div>
    </Card>
  );
}
