import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, PenLine, Terminal, X } from "lucide-react";
import { Card } from "../Card";
import type {
  ContextDiscoveryResult,
  DebugBundleDTO,
  EngineeringUnderstandingDTO,
} from "../../types/agent";
import { fetchUnderstanding, overrideStageResult } from "../../lib/api/workflows";
import { useAuth } from "../../app/auth-context";
import { RepositorySelector } from "./RepositorySelector";
import { SectionHeading } from "./EngineeringUnderstandingPanel";
import { InvestigationSummary } from "./InvestigationSummary";
import { AdvancedDetailsSection } from "./AdvancedDetailsSection";
import { ReasoningOverview } from "./ReasoningOverview";
import { DebugPanel } from "./DebugPanel";

/** `result.context_completeness` (falling back to the deprecated
 * `result.confidence` — same value, see `ContextDiscoveryResult`'s own
 * docstring) is a real number only once Context Discovery has actually
 * produced a scored result. While a run is still `awaiting_input`
 * (mid-clarification, no completed discovery result yet), `ContextExplorerPanel`
 * is still mounted from the in-flight `AgentStep.result` — see
 * `WorkflowPage`'s `discoveryResult` — so it can be `undefined`/`null`
 * here. `Math.round(undefined * 100)` silently renders "NaN%", which reads
 * as a real (nonsensical) score rather than "not computed yet." P3 fix:
 * format defensively and say so honestly instead.
 *
 * Named for what the number actually is — evidence/context completeness,
 * not confidence in an engineering conclusion — not for what it used to be
 * called. */
function formatCompleteness(completeness: number | null | undefined): string {
  if (typeof completeness !== "number" || Number.isNaN(completeness)) {
    return "context completeness not yet available";
  }
  return `${Math.round(completeness * 100)}% context completeness`;
}

interface ContextExplorerPanelProps {
  workflowId: string;
  result: ContextDiscoveryResult;
  /** A human's saved correction, if any. `result` is the AI's own unedited
   * output, so the corrected value only exists here — showing `result` alone
   * made a saved correction look like it had been discarded. */
  humanOverride?: Record<string, unknown> | null;
  /** Called after a correction is saved so the caller can refresh the
   * workflow (the next stage reads the corrected value via
   * get_stage_result() as soon as this commits — no other state changes). */
  onOverridden: () => void;
}

/** The review UI for Context Discovery's output, shown during the approval
 * transition between the context_discovery and planning stages.
 *
 * The default view is the Engineering Understanding projection — a curated
 * summary of what Discovery concluded, rendered as a natural-language
 * engineering brief rather than raw retrieval internals.
 *
 * The edit affordance stays deliberately narrow: a human corrects the
 * `graph_context_text` blob (the actual text Planning's prompt is built from)
 * rather than editing the structured findings — those are evidence-backed
 * facts, and hand-editing them would break the provenance the rest of this
 * panel is built on. */
export function ContextExplorerPanel({
  workflowId,
  result,
  humanOverride,
  onOverridden,
}: ContextExplorerPanelProps) {
  const { token } = useAuth();
  const [isEditing, setIsEditing] = useState(false);

  // Engineering Understanding projection — fetched from the backend endpoint.
  const [understanding, setUnderstanding] = useState<EngineeringUnderstandingDTO | null>(null);
  const [isLoadingUnderstanding, setIsLoadingUnderstanding] = useState(false);
  const [understandingError, setUnderstandingError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setIsLoadingUnderstanding(true);
    setUnderstandingError(null);

    fetchUnderstanding(token, workflowId, false, controller.signal)
      .then((dto) => {
        setUnderstanding(dto);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setUnderstandingError(
          err instanceof Error ? err.message : "Failed to load understanding.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoadingUnderstanding(false);
      });

    return () => controller.abort();
  }, [token, workflowId]);

  // Debug (Level 3) — its own, separate fetch, only ever made once an
  // engineer explicitly expands the Debug section. `debug_bundle` is null
  // on the default (non-debug) response, so this genuinely costs a second
  // round trip rather than being data already sitting unused in memory.
  const [debugBundle, setDebugBundle] = useState<DebugBundleDTO | null>(null);
  const [isLoadingDebug, setIsLoadingDebug] = useState(false);
  const [debugError, setDebugError] = useState<string | null>(null);

  const handleExpandDebug = () => {
    if (!token || debugBundle) return;
    setIsLoadingDebug(true);
    setDebugError(null);
    fetchUnderstanding(token, workflowId, true)
      .then((dto) => {
        setDebugBundle(dto.debug_bundle);
      })
      .catch((err) => {
        setDebugError(err instanceof Error ? err.message : "Failed to load debug information.");
      })
      .finally(() => {
        setIsLoadingDebug(false);
      });
  };

  // What Planning will actually receive: the human's correction when one exists,
  // otherwise the agent's own text. Kept explicitly separate from `result` so the
  // panel can label which of the two the user is looking at.
  const correctedContext =
    typeof humanOverride?.graph_context_text === "string" ? humanOverride.graph_context_text : null;
  const effectiveContext = correctedContext ?? result.graph_context_text;
  const [draft, setDraft] = useState(effectiveContext);
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

  return (
    <Card
      title="Context Explorer"
      description="Engineering understanding of what Context Discovery concluded."
      action={
        !isEditing && (
          <button
            type="button"
            onClick={() => {
              setDraft(effectiveContext);
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
        {/* Engineering Understanding projection */}
        {isLoadingUnderstanding && (
          <div className="flex items-center gap-2 py-4 text-xs text-fg-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading engineering understanding…
          </div>
        )}
        {understandingError && (
          <div className="rounded-lg border border-warning-line/30 bg-warning-bg px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-xs text-warning-fg">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {/* P2 fix — this used to render the raw backend error message
                  verbatim (e.g. "No completed context discovery result for
                  this workflow"), which directly contradicts the readiness/
                  confidence line rendered right below it: `result` (this
                  component's own prop) is always real, completed data —
                  this component never mounts without it — so a failure
                  fetching the *separate*, richer `/understanding` DTO is
                  never "there is no result," only "the fuller analysis
                  didn't load." Same underlying error, honest framing. */}
              Couldn&apos;t load the full engineering understanding — the readiness and evidence
              below are still accurate. ({understandingError})
            </p>
          </div>
        )}
        {/* Node 1 of the investigation story: "what was it trying to
            determine, and how sure is it?" Sourced from `result` (always
            present) plus `understanding` (nullable — this node degrades
            gracefully while the async fetch is loading or if it failed,
            same resilience contract the rest of this panel already
            follows). Replaces the old plain readiness pill. */}
        <ReasoningOverview result={result} understanding={understanding} />

        {/* BUDGET_EXHAUSTED is the one completion_status that must never
            read like a normal readiness banner — the audit's own finding
            was that a cycle-budget cutoff used to be indistinguishable
            from genuine exhaustion. This is the explicit, first-screen
            correction: what stopped it, that it isn't "everything was
            tried," and where to look for what's still unknown. */}
        {result.completion_status === "BUDGET_EXHAUSTED" && (
          <div className="rounded-lg border border-warning-line/40 bg-warning-bg px-3 py-2.5">
            <p className="flex items-start gap-1.5 text-xs font-medium text-warning-fg">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Investigation stopped because it reached its cycle limit — not because every
              avenue was exhausted.
            </p>
            <p className="mt-1 pl-5 text-[11px] text-warning-fg/80">
              What's below reflects what it found in the time it had, at{" "}
              {formatCompleteness(result.context_completeness ?? result.confidence)} and{" "}
              {result.readiness.toLowerCase()} readiness. See &ldquo;Still uncertain&rdquo; below
              for exactly what&apos;s still open.
            </p>
          </div>
        )}

        {understanding && (
          <>
            {/* The primary screen: what GraphForge found, how sure it is,
                what's supported, what's still uncertain, and what to do
                next — everything a reader (technical or not) needs
                without opening anything. The raw investigation log, the
                evidence-classification ledger, and the hypothesis/
                contradiction breakdown are real and valuable but are
                implementation detail for *this* question, not part of
                answering it — they live in Advanced Details below. */}
            <InvestigationSummary result={result} understanding={understanding} />

            {/* Repository Selector — an adjustment to the narrative above,
                not the first thing an engineer sees. */}
            <RepositorySelector
              workflowId={workflowId}
              result={result}
              humanOverride={humanOverride}
              onOverridden={onOverridden}
            />

            <AdvancedDetailsSection
              dto={understanding}
              result={result}
              bundle={debugBundle}
              isLoading={isLoadingDebug}
              error={debugError}
              onExpand={handleExpandDebug}
            />

            <DebugPanel
              bundle={debugBundle}
              isLoading={isLoadingDebug}
              error={debugError}
              onExpand={handleExpandDebug}
            />
          </>
        )}

        {/* Technical: the raw text Planning's prompt is built from — a
            deliberately separate, clearly-labelled technical section, not
            part of the curated Engineering Understanding narrative above. */}
        <section className="rounded-lg border border-line-muted bg-canvas px-3 py-2.5">
          <SectionHeading icon={Terminal}>Technical: Graph Context</SectionHeading>
          <p className="mt-0.5 text-[11px] text-fg-subtle">
            The raw text Planning's prompt is built from. For advanced correction only.
          </p>
          <div className="mt-2">
            {isEditing ? (
              <div className="flex flex-col gap-2">
                <label
                  htmlFor="context-correction"
                  className="text-xs font-medium text-fg-secondary"
                >
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
                  {correctedContext !== null && (
                    <span className="ml-1.5 rounded bg-accent-bg px-1.5 py-0.5 font-normal text-accent-fg">
                      edited by you
                    </span>
                  )}
                </summary>
                {correctedContext !== null && (
                  <p className="mt-2 text-xs text-fg-muted">
                    This is your correction — it&apos;s what Planning will read. The agent&apos;s
                    original text is kept unchanged on the run record.
                  </p>
                )}
                <p className="mt-2 rounded-lg bg-canvas p-3 font-mono text-xs whitespace-pre-wrap text-fg-secondary">
                  {effectiveContext || "No graph context text."}
                </p>
              </details>
            )}
          </div>
        </section>
      </div>
    </Card>
  );
}
