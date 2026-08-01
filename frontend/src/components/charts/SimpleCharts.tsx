/**
 * Minimal, dependency-free chart primitives for the Reports dashboard.
 *
 * The frontend has no charting library (see package.json) — existing graph
 * visualization uses @xyflow/react instead, which is the wrong tool for a
 * bar/line chart. Rather than add a new dependency for a handful of simple
 * shapes, these render directly to SVG.
 */

interface BarDatum {
  label: string;
  value: number;
}

interface BarChartProps {
  data: BarDatum[];
  color?: string;
  valueFormatter?: (value: number) => string;
  height?: number;
}

/** Vertical bar chart — cost/tokens per day, components per repository. */
export function BarChart({ data, color = "var(--gf-info-fg, #6366f1)", valueFormatter, height = 220 }: BarChartProps) {
  if (data.length === 0) {
    return <EmptyChart height={height} />;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  const barWidth = 100 / data.length;

  return (
    <div className="flex flex-col gap-2">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
        {data.map((d, i) => {
          const barHeight = (d.value / max) * (height - 20);
          const x = i * barWidth + barWidth * 0.15;
          const w = barWidth * 0.7;
          const y = height - 20 - barHeight;
          return (
            <g key={d.label + i}>
              <rect x={x} y={y} width={w} height={barHeight} rx={0.6} fill={color} opacity={0.85}>
                <title>
                  {d.label}: {valueFormatter ? valueFormatter(d.value) : d.value}
                </title>
              </rect>
            </g>
          );
        })}
        <line x1={0} y1={height - 20} x2={100} y2={height - 20} stroke="currentColor" strokeOpacity={0.15} strokeWidth={0.3} />
      </svg>
      <ChartLabels data={data} />
    </div>
  );
}

interface LineChartProps {
  data: BarDatum[];
  color?: string;
  valueFormatter?: (value: number) => string;
  height?: number;
}

/** Simple line/area chart — tokens over time. */
export function LineChart({ data, color = "var(--gf-success-fg, #22c55e)", valueFormatter, height = 220 }: LineChartProps) {
  if (data.length === 0) {
    return <EmptyChart height={height} />;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  const step = data.length > 1 ? 100 / (data.length - 1) : 0;
  const points = data.map((d, i) => {
    const x = data.length > 1 ? i * step : 50;
    const y = height - 20 - (d.value / max) * (height - 20);
    return { x, y, d };
  });
  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${height - 20} L ${points[0].x} ${height - 20} Z`;

  return (
    <div className="flex flex-col gap-2">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
        <path d={areaPath} fill={color} opacity={0.15} />
        <path d={linePath} fill="none" stroke={color} strokeWidth={0.6} vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={0.8} fill={color}>
            <title>
              {p.d.label}: {valueFormatter ? valueFormatter(p.d.value) : p.d.value}
            </title>
          </circle>
        ))}
        <line x1={0} y1={height - 20} x2={100} y2={height - 20} stroke="currentColor" strokeOpacity={0.15} strokeWidth={0.3} />
      </svg>
      <ChartLabels data={data} />
    </div>
  );
}

interface HorizontalBarDatum {
  label: string;
  value: number;
  color?: string;
}

interface HorizontalBarChartProps {
  data: HorizontalBarDatum[];
  valueFormatter?: (value: number) => string;
  defaultColor?: string;
}

/** Horizontal bar chart — cost by provider/stage (label + proportional bar
 * + value), sorted by the caller. */
export function HorizontalBarChart({
  data,
  valueFormatter,
  defaultColor = "var(--gf-info-fg, #6366f1)",
}: HorizontalBarChartProps) {
  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">No data to show.</p>;
  }
  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-3">
          <span className="w-28 shrink-0 truncate text-xs text-fg-muted" title={d.label}>
            {d.label}
          </span>
          <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-neutral-bg">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max((d.value / max) * 100, 2)}%`,
                backgroundColor: d.color ?? defaultColor,
              }}
            />
          </div>
          <span className="w-20 shrink-0 text-right text-xs tabular-nums text-fg-secondary">
            {valueFormatter ? valueFormatter(d.value) : d.value}
          </span>
        </div>
      ))}
    </div>
  );
}

interface StackedBarDatum {
  label: string;
  succeeded: number;
  failed: number;
}

/** Two-series horizontal stacked bar — run success/failure per stage. */
export function StackedBarChart({ data }: { data: StackedBarDatum[] }) {
  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">No data to show.</p>;
  }
  const max = Math.max(...data.map((d) => d.succeeded + d.failed), 1);

  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => {
        const total = d.succeeded + d.failed;
        return (
          <div key={d.label} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-xs text-fg-muted" title={d.label}>
              {d.label}
            </span>
            <div className="flex h-2.5 flex-1 overflow-hidden rounded-full bg-neutral-bg">
              <div
                className="h-full bg-success-fg"
                style={{ width: `${(d.succeeded / max) * 100}%` }}
                title={`${d.succeeded} succeeded`}
              />
              <div
                className="h-full bg-danger-fg"
                style={{ width: `${(d.failed / max) * 100}%` }}
                title={`${d.failed} failed`}
              />
            </div>
            <span className="w-24 shrink-0 text-right text-xs tabular-nums text-fg-secondary">
              {total} run{total === 1 ? "" : "s"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ChartLabels({ data }: { data: BarDatum[] }) {
  // Thin out labels so they don't overlap on wide date ranges.
  const maxLabels = 10;
  const stride = Math.max(1, Math.ceil(data.length / maxLabels));
  return (
    <div className="flex justify-between text-[10px] text-fg-muted">
      {data.map((d, i) =>
        i % stride === 0 || i === data.length - 1 ? (
          <span key={d.label + i} className="truncate">
            {d.label.slice(5)}
          </span>
        ) : null,
      )}
    </div>
  );
}

function EmptyChart({ height }: { height: number }) {
  return (
    <div
      className="flex items-center justify-center text-sm text-fg-muted"
      style={{ height }}
    >
      No data to show.
    </div>
  );
}
