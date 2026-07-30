import { useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CircleSlash,
  PenLine,
  Search,
  X,
  XCircle,
} from "lucide-react";
import { Card } from "../Card";
import type {
  CapabilityBreakdown,
  ContextDiscoveryResult,
  DiscoveryGap,
  FindingGroup,
  InvestigationStep,
  TranscriptEntry,
} from "../../types/agent";
import { overrideStageResult } from "../../lib/api/workflows";
import { useAuth } from "../../app/auth-context";
import { RepositorySelector } from "./RepositorySelector";

/** How the reasoning engine narrates itself. `intent` lines are what it was
 * about to do; `observation` lines are what it found. Rendering them
 * differently is what makes the panel read as an investigation rather than a
 * log dump. */
const TRANSCRIPT_STYLE: Record<TranscriptEntry["kind"], string> = {
  intent: "text-fg-muted italic",
  observation: "text-fg-secondary",
  question: "text-warning-fg font-medium",
  answer: "text-accent-fg font-medium",
  conclusion: "text-fg font-medium",
};

const OUTCOME_MARK: Record<InvestigationStep["outcome"], { icon: typeof Check; tone: string }> = {
  success: { icon: Check, tone: "text-success-fg" },
  not_found: { icon: CircleSlash, tone: "text-fg-muted" },
  unavailable: { icon: CircleSlash, tone: "text-fg-muted" },
  failed: { icon: X, tone: "text-danger-fg" },
};

/** The investigation, in order, with what the engine was trying to learn at
 * each step. This is the "what did you actually search?" answer — the thing a
 * user needs when discovery reports something missing. */
function InvestigationTrail({ steps }: { steps: InvestigationStep[] }) {
  if (steps.length === 0) return null;
  return (
    <details className="group rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
      <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-fg-secondary">
        <Search className="h-3.5 w-3.5" aria-hidden="true" />
        What I searched ({steps.length} step{steps.length === 1 ? "" : "s"})
      </summary>
      <ol className="mt-2 flex flex-col gap-2">
        {steps.map((step) => {
          const { icon: Icon, tone } = OUTCOME_MARK[step.outcome];
          return (
            <li key={step.evidence_id} className="flex items-start gap-2 text-xs">
              <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${tone}`} aria-hidden="true" />
              <div className="flex flex-col">
                <span className="text-fg-secondary">{step.summary}</span>
                {step.intent && <span className="text-fg-subtle italic">{step.intent}</span>}
                <span className="text-fg-subtle">
                  {step.provider} · {step.action}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </details>
  );
}

/** Per-capability confidence with the ✓/✗ signals that produce the number.
 * A score is never shown on its own: every percentage is followed by the
 * decomposition it was computed from, and each satisfied signal names the
 * evidence behind it. */
function ConfidenceBreakdown({ items }: { items: CapabilityBreakdown[] }) {
  const applicable = items.filter((item) => item.necessity !== "not_applicable");
  if (applicable.length === 0) return null;

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-fg-secondary">Confidence, and why</p>
      {applicable.map((item) => (
        <div key={item.capability} className="rounded-lg bg-surface-raised px-3 py-2">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-xs font-medium text-fg-secondary">
              {item.label}
              {item.necessity === "recommended" && (
                <span className="ml-1.5 text-fg-subtle">(optional)</span>
              )}
            </p>
            <span
              className={`text-sm font-semibold ${
                item.satisfied ? "text-success-fg" : "text-warning-fg"
              }`}
            >
              {Math.round(item.score * 100)}%
            </span>
          </div>
          <ul className="mt-1.5 flex flex-col gap-1">
            {item.signals.map((signal) => (
              <li key={signal.label} className="flex items-start gap-1.5 text-xs">
                {signal.satisfied ? (
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-success-fg" aria-hidden="true" />
                ) : (
                  <X className="mt-0.5 h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
                )}
                <span className={signal.satisfied ? "text-fg-secondary" : "text-fg-muted"}>
                  {signal.label}
                  {!signal.satisfied && signal.detail && (
                    <span className="text-fg-subtle"> — {signal.detail}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Facts grouped by kind, each with the evidence that established it — the
 * "why do you believe this?" view. An unverified item is a human claim that
 * investigation could not corroborate, and is labelled as such rather than
 * being displayed as knowledge. */
function Findings({ groups }: { groups: FindingGroup[] }) {
  const visible = groups.filter((g) => g.items.length > 0);
  if (visible.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-semibold text-fg-secondary">What I found</p>
      {visible.map((group) => (
        <div key={group.kind} className="rounded-lg bg-surface-raised px-3 py-2">
          <p className="text-xs font-medium text-fg-secondary capitalize">
            {group.kind.replace(/_/g, " ")}
            {group.total > group.items.length && (
              <span className="ml-1.5 font-normal text-fg-subtle">
                showing {group.items.length} of {group.total}
              </span>
            )}
          </p>
          <ul className="mt-1 flex flex-col gap-1">
            {group.items.map((item) => (
              <li key={item.fact_id} className="text-xs">
                <span className="text-fg-secondary">{item.subject}</span>
                {!item.verified && (
                  <span className="ml-1.5 rounded bg-warning-bg px-1 py-0.5 text-warning-fg">
                    unverified claim
                  </span>
                )}
                {item.evidence && (
                  <span className="block text-fg-subtle">↳ {item.evidence.summary}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** What's missing, why it matters, and what to do about it. Remediation is
 * rendered as guidance here — deliberately never as an answer option in the
 * clarification banner. */
function Gaps({ gaps }: { gaps: DiscoveryGap[] }) {
  const open = gaps.filter((g) => g.status !== "verified");
  if (open.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-semibold text-fg-secondary">What's still missing</p>
      {open.map((gap) => {
        const blocking = gap.severity === "blocking";
        const Icon = blocking ? XCircle : AlertTriangle;
        return (
          <div
            key={gap.gap_id}
            className={`rounded-lg border px-3 py-2 ${
              blocking
                ? "border-danger-line/30 bg-danger-bg"
                : "border-warning-line/30 bg-warning-bg"
            }`}
          >
            <p
              className={`flex items-start gap-1.5 text-xs font-medium ${
                blocking ? "text-danger-fg" : "text-warning-fg"
              }`}
            >
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {gap.summary}
            </p>
            <p className="mt-1 pl-5 text-xs text-fg-muted">{gap.why}</p>
            {gap.missing.length > 0 && (
              <ul className="mt-1 flex flex-col gap-0.5 pl-5">
                {gap.missing.map((line) => (
                  <li key={line} className="text-xs text-fg-subtle">
                    · {line}
                  </li>
                ))}
              </ul>
            )}
            {gap.resolution_note && (
              <p className="mt-1 pl-5 text-xs text-fg-muted">{gap.resolution_note}</p>
            )}
            {gap.recommended_action.length > 0 && (
              <p className="mt-1 pl-5 text-xs font-medium text-fg-secondary">
                Try: {gap.recommended_action.join(" · ")}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
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
 * Structured to answer, in order: what did you conclude, how did you get
 * there, what do you actually know and how do you know it, and what's still
 * missing. That ordering is the point — the old panel led with counts
 * ("3 repositories, 14 components"), which told a reviewer what was retrieved
 * but nothing about whether it should be trusted.
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

  // What Planning will actually receive: the human's correction when one exists,
  // otherwise the agent's own text. Kept explicitly separate from `result` so the
  // panel can label which of the two the user is looking at.
  const correctedContext =
    typeof humanOverride?.graph_context_text === "string" ? humanOverride.graph_context_text : null;
  const effectiveContext = correctedContext ?? result.graph_context_text;
  const [draft, setDraft] = useState(effectiveContext);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const report = result.discovery_report;

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

  const readinessTone =
    result.readiness === "READY"
      ? "text-success-fg"
      : result.readiness === "PARTIAL"
        ? "text-warning-fg"
        : "text-danger-fg";

  return (
    <Card
      title="Context Explorer"
      description="How Context Discovery reached its conclusion — and what it could not establish."
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
        {/* The verdict, in the engine's own words. */}
        <div className="flex flex-col gap-1.5 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <div className="flex items-center justify-between gap-3">
            <span className={`text-xs font-semibold ${readinessTone}`}>
              {result.readiness === "READY" ? (
                <CheckCircle2 className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <AlertTriangle className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
              )}
              {result.readiness}
            </span>
            <span className="text-xs font-medium text-fg-muted">
              {Math.round(result.confidence * 100)}% confidence
            </span>
          </div>
          {report?.headline && <p className="text-xs text-fg-secondary">{report.headline}</p>}
        </div>

        <RepositorySelector
          workflowId={workflowId}
          result={result}
          humanOverride={humanOverride}
          onOverridden={onOverridden}
        />

        {/* How it got there. */}
        {report?.transcript && report.transcript.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs font-semibold text-fg-secondary">How I worked it out</p>
            <ol className="flex flex-col gap-1 border-l border-line-muted pl-3">
              {report.transcript.map((entry, i) => (
                <li key={i} className={`text-xs ${TRANSCRIPT_STYLE[entry.kind]}`}>
                  {entry.text}
                </li>
              ))}
            </ol>
          </div>
        )}

        {report?.investigation && <InvestigationTrail steps={report.investigation} />}
        {report?.confidence_breakdown && (
          <ConfidenceBreakdown items={report.confidence_breakdown} />
        )}
        {report?.findings && <Findings groups={report.findings} />}
        {report?.gaps && <Gaps gaps={report.gaps} />}

        {result.assumptions.length > 0 && (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-semibold text-fg-secondary">Assumptions I'm proceeding on</p>
            <ul className="flex flex-col gap-0.5">
              {result.assumptions.map((assumption) => (
                <li key={assumption} className="text-xs text-fg-muted">
                  · {assumption}
                </li>
              ))}
            </ul>
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
    </Card>
  );
}
