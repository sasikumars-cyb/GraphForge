import { AlertTriangle, CircleOff, Sparkles } from "lucide-react";
import type { SynthesisRunState } from "../../lib/api/reports";

// ---------------------------------------------------------------------------
// ADR 0024 §11 — the four-state model, rendered as four visually distinct
// notices. A missing reasoning summary must never look like "no hypotheses
// found" (which implies the agent investigated and found none): NOT_RUN,
// FAILED, and COMPLETED_EMPTY are three different situations with three
// different copy variants, shared identically by Hypotheses and
// Contradictions so the two sections can never drift apart on wording.
// ---------------------------------------------------------------------------

const COPY: Record<
  Exclude<SynthesisRunState, "completed">,
  { icon: typeof AlertTriangle; title: string; body: string; tone: "muted" | "warning" }
> = {
  not_run: {
    icon: CircleOff,
    title: "Reasoning synthesis was not recorded",
    body: "This investigation has no reasoning synthesis result — either it predates this feature, or nothing was ever gathered to reason over.",
    tone: "muted",
  },
  failed: {
    icon: AlertTriangle,
    title: "Reasoning synthesis failed",
    body: "Synthesis ran but did not complete for this investigation, falling back to evidence-only findings. This is not the same as “no hypotheses found.”",
    tone: "warning",
  },
  completed_empty: {
    icon: Sparkles,
    title: "Investigation converged without competing hypotheses",
    body: "Reasoning synthesis ran and completed — it simply found nothing to hypothesize about here. A real, sometimes positive outcome, not a gap.",
    tone: "muted",
  },
};

export function SynthesisStateNotice({ state }: { state: SynthesisRunState }) {
  if (state === "completed") return null;
  const { icon: Icon, title, body, tone } = COPY[state];
  const toneClass =
    tone === "warning"
      ? "border-warning-line/40 bg-warning-bg text-warning-fg"
      : "border-line-muted bg-surface-raised text-fg-muted";
  return (
    <div className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${toneClass}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div>
        <p className="text-xs font-semibold">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed opacity-90">{body}</p>
      </div>
    </div>
  );
}
