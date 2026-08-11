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
      <Card title="Confidence">
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

  return (
    <Card title="Confidence">
      <div className="flex items-baseline gap-3">
        <span
          className={`font-display text-3xl font-semibold tabular-nums ${
            droppedStage ? "text-danger-fg" : "text-fg"
          }`}
        >
          {confidence.current !== null ? `${Math.round(confidence.current * 100)}%` : "—"}
        </span>
        <span className="text-xs text-fg-muted">{confidence.summary_sentence}</span>
      </div>
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
