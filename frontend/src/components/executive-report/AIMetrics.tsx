import { Cpu, Zap, Server } from "lucide-react";
import { Card } from "../Card";
import { CollapsibleSection } from "./CollapsibleSection";
import type { MetricsSummary } from "../../lib/executiveReportMapper";

interface AIMetricsProps {
  data: MetricsSummary;
}

/**
 * AI usage metrics — model, tokens, latency, and cost per stage,
 * with summary stats at the top.
 */
export function AIMetrics({ data }: AIMetricsProps) {
  return (
    <CollapsibleSection title="AI Usage Metrics">
      <Card>
        {/* Summary stats */}
        <div className="mb-4 grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <Cpu className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                Primary Model
              </span>
            </div>
            <p className="mt-0.5 truncate font-mono text-sm font-semibold text-fg">
              {data.primaryModel}
            </p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <Zap className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                LLM Calls
              </span>
            </div>
            <p className="mt-0.5 font-mono text-sm font-semibold text-fg">{data.totalCalls}</p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <Server className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                Provider
              </span>
            </div>
            <p className="mt-0.5 truncate font-mono text-sm font-semibold text-fg">
              {data.primaryProvider}
            </p>
          </div>
        </div>

        {/* Per-stage table */}
        {data.rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[0.65rem] uppercase tracking-wide text-fg-muted">
                  <th className="px-2 py-2 font-medium">Stage</th>
                  <th className="px-2 py-2 font-medium">Model</th>
                  <th className="px-2 py-2 font-medium">Tokens</th>
                  <th className="px-2 py-2 font-medium">Latency</th>
                  <th className="px-2 py-2 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-muted">
                {data.rows.map((row) => (
                  <tr key={row.stage} className="text-fg-secondary">
                    <td className="px-2 py-2 font-medium text-fg">{row.stage}</td>
                    <td className="px-2 py-2 font-mono text-fg-muted">{row.model}</td>
                    <td className="px-2 py-2 font-mono tabular-nums">{row.tokens}</td>
                    <td className="px-2 py-2 font-mono tabular-nums">{row.latency}</td>
                    <td className="px-2 py-2 font-mono tabular-nums">{row.cost}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </CollapsibleSection>
  );
}
