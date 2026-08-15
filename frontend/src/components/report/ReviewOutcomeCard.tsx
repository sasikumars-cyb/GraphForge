import { AlertOctagon, CheckCircle2, CircleHelp, Compass } from "lucide-react";
import type { ReviewOutcomeVM } from "../../lib/api/reports";
import { Card } from "../Card";

/** [ Engineering Review Outcome ] — the decision this document exists to
 * communicate, in words: the outcome, why it was reached, and what to
 * actually do next.
 *
 * Everything rendered here is already decided by the backend
 * (`ReviewOutcomeVM`) — this component chooses no wording, derives no
 * verdict, and recounts nothing. In particular the blocking/advisory
 * numbers are the backend's own counts over the one open-item list, the
 * same ones "What's next" renders. */
const TONE: Record<
  string,
  { icon: typeof CheckCircle2; border: string; bg: string; fg: string }
> = {
  ready: {
    icon: CheckCircle2,
    border: "border-success-line/50",
    bg: "bg-success-bg/40",
    fg: "text-success-fg",
  },
  needs_revision: {
    icon: Compass,
    border: "border-warning-line/50",
    bg: "bg-warning-bg/40",
    fg: "text-warning-fg",
  },
  not_ready: {
    icon: AlertOctagon,
    border: "border-danger-line/50",
    bg: "bg-danger-bg/40",
    fg: "text-danger-fg",
  },
  unknown: {
    icon: CircleHelp,
    border: "border-line-muted",
    bg: "bg-surface-raised",
    fg: "text-fg-muted",
  },
};

export function ReviewOutcomeCard({ outcome }: { outcome: ReviewOutcomeVM }) {
  const tone = TONE[outcome.readiness] ?? TONE.unknown;
  const Icon = tone.icon;

  return (
    <Card title="Engineering Review outcome">
      <div className={`rounded-lg border-2 ${tone.border} ${tone.bg} p-4`}>
        <p className={`flex items-center gap-2 font-display text-base font-semibold ${tone.fg}`}>
          <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
          Engineering Review Outcome: {outcome.outcome_label}
        </p>
        <p className="mt-2 text-sm leading-relaxed text-fg-secondary">
          {outcome.outcome_statement}
        </p>
      </div>

      <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-fg-subtle">Reason</p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {outcome.reasons.map((reason, i) => (
          <li key={i} className="flex gap-2 text-xs leading-relaxed text-fg-secondary">
            <span aria-hidden="true">·</span>
            <span>{reason}</span>
          </li>
        ))}
      </ul>

      <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-fg-subtle">
        Recommended action
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-fg">{outcome.recommendation}</p>

      <p className="mt-3 text-[11px] text-fg-subtle">
        {outcome.blocking_count} blocking, {outcome.advisory_count} advisory.
        {outcome.readiness !== outcome.reported_readiness && (
          <>
            {" "}
            Engineering Review itself reported “{outcome.reported_readiness.replace(/_/g, " ")}”;
            this document renders the more conservative outcome because blocking items remain
            open.
          </>
        )}
      </p>
    </Card>
  );
}
