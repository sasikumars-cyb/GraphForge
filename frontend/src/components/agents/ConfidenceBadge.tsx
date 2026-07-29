import type { Confidence } from "../../types/agent";

interface ConfidenceBadgeProps {
  confidence: Confidence;
  showReasoning?: boolean;
}

function scoreColor(score: number | null): string {
  if (score === null) return "bg-neutral-bg text-fg-secondary ring-line";
  if (score >= 0.8) return "bg-success-bg text-success-fg ring-success-line/30";
  if (score >= 0.5) return "bg-warning-bg text-warning-fg ring-warning-line/30";
  return "bg-danger-bg text-danger-fg ring-danger-line/30";
}

function scoreLabel(score: number | null): string {
  if (score === null) return "—";
  return `${Math.round(score * 100)}%`;
}

/** Displays a confidence score as a colored pill with optional reasoning tooltip. */
export function ConfidenceBadge({ confidence, showReasoning = false }: ConfidenceBadgeProps) {
  return (
    <div className="inline-flex flex-col gap-1">
      <span
        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${scoreColor(confidence.score)}`}
        title={confidence.reasoning || undefined}
        aria-label={`Confidence: ${scoreLabel(confidence.score)}`}
      >
        {scoreLabel(confidence.score)}
      </span>
      {showReasoning && confidence.reasoning && (
        <p className="text-xs text-fg-muted">{confidence.reasoning}</p>
      )}
    </div>
  );
}
