import type { ConfidenceSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { LineChart } from "../charts/SimpleCharts";
import { EmptyState } from "../EmptyState";

/** [ Confidence / Readiness ] — "how confident is it", visualized as a
 * journey across stages (ADR 0024 §4/§10), not just a static percentage.
 * A real drop is never smoothed away — `summary_sentence` states it
 * plainly, and the dropped point's own bar/line segment is the same data,
 * just drawn. */
export function ConfidenceJourneyCard({ confidence }: { confidence: ConfidenceSectionVM }) {
  if (confidence.availability.status === "unavailable") {
    return (
      <Card title="Confidence &amp; readiness">
        <EmptyState
          title="No confidence score yet"
          description={confidence.availability.reason ?? "No stage has produced a confidence score."}
        />
      </Card>
    );
  }

  const chartData = confidence.points
    .filter((p) => p.confidence !== null)
    .map((p) => ({ label: p.label, value: Math.round((p.confidence ?? 0) * 100) }));
  const droppedStage = confidence.points.find((p) => p.dropped);

  const { breakdown } = confidence;

  return (
    <Card title="Confidence &amp; readiness">
      {/* Two numbers, two labels, always — the headline percentage is the
          investigation's overall resolution confidence, and the strongest
          hypothesis's own confidence sits beside it as a separate,
          separately-labelled measure. Never one number, never unlabelled:
          a 95% hypothesis inside a 45% investigation is a real state, and
          the backend's `divergence_note` explains it below. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
            {breakdown.overall_label}
          </p>
          <span
            className={`font-display text-3xl font-semibold tabular-nums ${
              droppedStage ? "text-danger-fg" : "text-fg"
            }`}
          >
            {breakdown.overall !== null ? `${Math.round(breakdown.overall * 100)}%` : "—"}
          </span>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">{breakdown.overall_basis}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-subtle">
            {breakdown.top_hypothesis_label}
          </p>
          <span className="font-display text-3xl font-semibold tabular-nums text-fg-secondary">
            {breakdown.top_hypothesis_confidence !== null
              ? `${Math.round(breakdown.top_hypothesis_confidence * 100)}%`
              : "—"}
          </span>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">
            {breakdown.top_hypothesis_statement
              ? `Confidence in one specific candidate explanation: ${breakdown.top_hypothesis_statement}`
              : "No candidate explanation was recorded."}
          </p>
        </div>
      </div>
      {breakdown.divergence_note && (
        <p className="mt-3 rounded-lg border border-info-line/40 bg-info-bg/40 px-3 py-2 text-xs leading-relaxed text-fg-secondary">
          {breakdown.divergence_note}
        </p>
      )}
      <p className="mt-3 text-xs text-fg-muted">{confidence.summary_sentence}</p>
      {chartData.length > 1 && (
        <div className="mt-4">
          <LineChart
            data={chartData}
            color={droppedStage ? "var(--gf-danger-fg, #f43f5e)" : "var(--gf-info-fg, #096e9c)"}
            valueFormatter={(v) => `${Math.round(v)}%`}
            height={140}
            label="Confidence by stage"
          />
        </div>
      )}
    </Card>
  );
}
