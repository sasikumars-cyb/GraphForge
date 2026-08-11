/**
 * Minimal, dependency-free chart primitives for the Reports dashboard.
 *
 * The frontend has no charting library (see package.json) — existing graph
 * visualization uses @xyflow/react instead, which is the wrong tool for a
 * bar/line chart. Rather than add a new dependency for a handful of simple
 * shapes, these render directly to SVG.
 *
 * Accessibility: an SVG `<title>` tooltip is reachable only by mouse, so
 * every chart here also emits a visually-hidden `<table>` of the same data
 * (see `ChartDataTable`). That table — not the tooltip — is the canonical
 * representation for keyboard and screen-reader users, and it is why each
 * chart is wrapped in a `<figure role="img">` with a summarising label
 * rather than left as an unlabelled graphic.
 */

interface BarDatum {
  label: string;
  value: number;
}

/** Formats a datum's label for the x-axis tick strip. Defaults to identity.
 *
 * This exists because the axis previously hardcoded `label.slice(5)` to trim
 * the `YYYY-` off ISO dates — which silently ate the first five characters of
 * every *non*-date label, and the same BarChart renders repository names (see
 * MetricsPage's "Repository Graph" card). Trimming is now the caller's
 * decision, since only the caller knows what its labels are. */
export type LabelFormatter = (label: string) => string;

const identityLabel: LabelFormatter = (label) => label;

/** The visually-hidden data table backing every chart. Chart values are
 * otherwise mouse-only (SVG `<title>`), which makes them unreachable by
 * keyboard and unreliable for screen readers. */
function ChartDataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: string[];
  rows: (string | number)[][];
}) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c} scope="col">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) =>
              j === 0 ? (
                <th key={j} scope="row">
                  {cell}
                </th>
              ) : (
                <td key={j}>{cell}</td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface BarChartProps {
  data: BarDatum[];
  color?: string;
  valueFormatter?: (value: number) => string;
  height?: number;
  /** Trims x-axis tick labels. Defaults to showing them unmodified. */
  labelFormatter?: LabelFormatter;
  /** Names the series for assistive tech, e.g. "Cost per day". */
  label?: string;
}

/** Vertical bar chart — cost/tokens per day, components per repository.
 * Defaults to the Iris brand accent: the single most common caller of
 * this default is Metrics' own headline "AI cost per day" series — the
 * one chart on the page that most deserves to read as "this is
 * GraphForge's own signal," not a generic info-blue bar. Callers with a
 * more specific semantic (a danger-toned failure count, an explicit
 * per-repository categorical color) still pass their own `color`. */
export function BarChart({
  data,
  color = "var(--gf-accent-fg, #4338ca)",
  valueFormatter,
  height = 220,
  labelFormatter = identityLabel,
  label = "Bar chart",
}: BarChartProps) {
  if (data.length === 0) {
    return <EmptyChart height={height} />;
  }
  const format = valueFormatter ?? String;
  const max = Math.max(...data.map((d) => d.value), 1);
  const barWidth = 100 / data.length;

  return (
    <figure className="m-0 flex flex-col gap-2" role="img" aria-label={`${label}. ${data.length} points, highest ${format(max)}.`}>
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        aria-hidden="true"
      >
        {data.map((d, i) => {
          const barHeight = (d.value / max) * (height - 20);
          const x = i * barWidth + barWidth * 0.15;
          const w = barWidth * 0.7;
          const y = height - 20 - barHeight;
          return (
            <g key={d.label + i}>
              <rect x={x} y={y} width={w} height={barHeight} rx={0.6} fill={color} opacity={0.85}>
                <title>
                  {d.label}: {format(d.value)}
                </title>
              </rect>
            </g>
          );
        })}
        <line x1={0} y1={height - 20} x2={100} y2={height - 20} stroke="currentColor" strokeOpacity={0.15} strokeWidth={0.3} />
      </svg>
      <ChartLabels data={data} labelFormatter={labelFormatter} />
      <ChartDataTable
        caption={label}
        columns={["Label", "Value"]}
        rows={data.map((d) => [d.label, format(d.value)])}
      />
    </figure>
  );
}

interface LineChartProps {
  data: BarDatum[];
  color?: string;
  valueFormatter?: (value: number) => string;
  height?: number;
  labelFormatter?: LabelFormatter;
  label?: string;
}

/** Simple line/area chart — tokens over time. Defaults to the analytical
 * "derived" tone (the same cool cyan-blue `ProvenanceTag kind="derived"`
 * uses) rather than success-green — a raw token count isn't a pass/fail
 * signal, and borrowing the green success color implied one that isn't
 * there. Explicit `color` overrides still win (e.g. ConfidenceJourneyCard
 * switches to danger-toned when confidence actually drops). */
export function LineChart({
  data,
  color = "var(--gf-info-fg, #096e9c)",
  valueFormatter,
  height = 220,
  labelFormatter = identityLabel,
  label = "Line chart",
}: LineChartProps) {
  if (data.length === 0) {
    return <EmptyChart height={height} />;
  }
  const format = valueFormatter ?? String;
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
    <figure className="m-0 flex flex-col gap-2" role="img" aria-label={`${label}. ${data.length} points, highest ${format(max)}.`}>
      <svg
        viewBox={`0 0 100 ${height}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        aria-hidden="true"
      >
        <path d={areaPath} fill={color} opacity={0.15} />
        <path d={linePath} fill="none" stroke={color} strokeWidth={0.6} vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          // `preserveAspectRatio="none"` stretches x independently of y, so a
          // <circle> renders as an ellipse. A 1-unit-wide <rect> sized in the
          // same distorted space is at least *predictably* a small tick rather
          // than a shape that changes proportion with the viewport width.
          <rect
            key={i}
            x={p.x - 0.4}
            y={p.y - 1.5}
            width={0.8}
            height={3}
            fill={color}
          >
            <title>
              {p.d.label}: {format(p.d.value)}
            </title>
          </rect>
        ))}
        <line x1={0} y1={height - 20} x2={100} y2={height - 20} stroke="currentColor" strokeOpacity={0.15} strokeWidth={0.3} />
      </svg>
      <ChartLabels data={data} labelFormatter={labelFormatter} />
      <ChartDataTable
        caption={label}
        columns={["Label", "Value"]}
        rows={data.map((d) => [d.label, format(d.value)])}
      />
    </figure>
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
  label?: string;
}

/** Horizontal bar chart — cost by provider/stage (label + proportional bar
 * + value), sorted by the caller. */
export function HorizontalBarChart({
  data,
  valueFormatter,
  defaultColor = "var(--gf-info-fg, #096e9c)",
  label = "Horizontal bar chart",
}: HorizontalBarChartProps) {
  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">No data to show.</p>;
  }
  const format = valueFormatter ?? String;
  const max = Math.max(...data.map((d) => d.value), 1);

  // Label and value are rendered as real text here (unlike the SVG charts),
  // so this is already readable by assistive tech — it needs no sr-only
  // duplicate, only a group label.
  return (
    <div className="flex flex-col gap-3" role="group" aria-label={label}>
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
            {format(d.value)}
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

/**
 * Two-series horizontal stacked bar — run success/failure per stage.
 *
 * Each bar is normalized to its own total, so what the eye compares is the
 * success *rate*, which is what the caller's card title promises. Scaling
 * every bar by the global max total instead (the previous behaviour) encoded
 * run *volume*: a stage with 2 runs and a 100% failure rate drew a shorter
 * red bar than a stage with 40 runs and a 5% failure rate, which inverts the
 * signal a reader is looking for. Volume hasn't been dropped — it stays as
 * the run count on the right, where it can't be confused for a proportion.
 */
export function StackedBarChart({
  data,
  label = "Run outcome by stage",
}: {
  data: StackedBarDatum[];
  label?: string;
}) {
  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">No data to show.</p>;
  }

  const rate = (d: StackedBarDatum) => {
    const total = d.succeeded + d.failed;
    return total === 0 ? 0 : Math.round((d.succeeded / total) * 100);
  };

  return (
    <figure className="m-0 flex flex-col gap-3" role="img" aria-label={label}>
      {data.map((d) => {
        const total = d.succeeded + d.failed;
        // A stage with no runs gets an empty track rather than a
        // divide-by-zero-width bar.
        const successPct = total === 0 ? 0 : (d.succeeded / total) * 100;
        const failedPct = total === 0 ? 0 : (d.failed / total) * 100;
        return (
          <div key={d.label} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-xs text-fg-muted" title={d.label}>
              {d.label}
            </span>
            <div className="flex h-2.5 flex-1 overflow-hidden rounded-full bg-neutral-bg">
              <div className="h-full bg-success-fg" style={{ width: `${successPct}%` }} />
              <div className="h-full bg-danger-fg" style={{ width: `${failedPct}%` }} />
            </div>
            {/* The rate in text, not just in colour — the two segments are
                otherwise distinguishable only by hue (WCAG 1.4.1), and the
                counts previously lived in `title` attributes on <div>s,
                which screen readers do not reliably announce. */}
            <span className="w-28 shrink-0 text-right text-xs tabular-nums text-fg-secondary">
              {total === 0 ? "no runs" : `${rate(d)}% of ${total}`}
            </span>
          </div>
        );
      })}
      <ChartDataTable
        caption={label}
        columns={["Stage", "Succeeded", "Failed", "Success rate"]}
        rows={data.map((d) => [
          d.label,
          d.succeeded,
          d.failed,
          d.succeeded + d.failed === 0 ? "n/a" : `${rate(d)}%`,
        ])}
      />
    </figure>
  );
}

function ChartLabels({
  data,
  labelFormatter,
}: {
  data: BarDatum[];
  labelFormatter: LabelFormatter;
}) {
  // Thin out labels so they don't overlap on wide date ranges.
  const maxLabels = 10;
  const stride = Math.max(1, Math.ceil(data.length / maxLabels));
  return (
    <div className="flex justify-between text-[10px] text-fg-muted" aria-hidden="true">
      {data.map((d, i) =>
        i % stride === 0 || i === data.length - 1 ? (
          <span key={d.label + i} className="truncate">
            {labelFormatter(d.label)}
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
