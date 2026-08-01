import type { ChartBar } from "../../lib/executiveReportMapper";

interface BarChartProps {
  title: string;
  bars: ChartBar[];
  /** CSS color class for the bar fill: "bg-accent-solid", "bg-info-solid", etc. */
  barColor?: string;
}

/**
 * Pure CSS horizontal bar chart — no external charting library needed.
 * Each bar scales proportionally to the max value in the dataset.
 */
export function BarChart({ title, bars, barColor = "bg-accent-solid" }: BarChartProps) {
  const maxValue = Math.max(...bars.map((b) => b.value), 1);

  return (
    <div className="mb-4">
      <h3 className="mb-2 text-xs font-semibold text-fg-secondary">{title}</h3>
      <div className="space-y-2" role="img" aria-label={title}>
        {bars.map((bar) => {
          const pct = maxValue > 0 ? (bar.value / maxValue) * 100 : 0;
          return (
            <div key={bar.label} className="flex items-center gap-2">
              <span className="w-28 flex-shrink-0 truncate text-[0.65rem] text-fg-muted">
                {bar.label}
              </span>
              <div className="relative h-4 flex-1 overflow-hidden rounded-sm bg-surface-raised">
                <div
                  className={`absolute inset-y-0 left-0 rounded-sm ${barColor}`}
                  style={{ width: `${Math.max(pct, 1)}%` }}
                  role="presentation"
                />
              </div>
              <span className="w-16 flex-shrink-0 text-right font-mono text-[0.65rem] tabular-nums text-fg-muted">
                {bar.formatted}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
