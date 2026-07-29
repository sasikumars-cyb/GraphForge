import { CheckCircle2, XCircle } from "lucide-react";
import { Card } from "../Card";
import type { Confidence, PlanningResult } from "../../types/agent";

interface PlanningConfidencePanelProps {
  confidence: Confidence;
  result: PlanningResult | undefined;
}

interface Factor {
  label: string;
  met: boolean;
  weight: "high" | "medium" | "low";
}

function scoreColor(score: number | null): string {
  if (score === null) return "text-fg-muted";
  if (score >= 0.8) return "text-success-fg";
  if (score >= 0.5) return "text-warning-fg";
  return "text-danger-fg";
}

function scoreBarColor(score: number | null): string {
  if (score === null) return "bg-line-strong";
  if (score >= 0.8) return "bg-success-solid";
  if (score >= 0.5) return "bg-warning-solid";
  return "bg-danger-solid";
}

function deriveFactors(result: PlanningResult | undefined): Factor[] {
  const reposIndexed = (result?.repositories_consulted?.length ?? 0) > 0;
  const graphUsed = result?.graph_context_used ?? false;
  const hasArchitecture = (result?.architecture_layers?.length ?? 0) > 0;
  const hasPlan = (result?.implementation_steps?.length ?? 0) > 0;

  return [
    {
      label: "Business requirements provided",
      met: true,
      weight: "high",
    },
    {
      label: "Implementation plan generated",
      met: hasPlan,
      weight: "high",
    },
    {
      label: "Architecture layers defined",
      met: hasArchitecture,
      weight: "medium",
    },
    {
      label: "Repositories indexed",
      met: reposIndexed,
      weight: "high",
    },
    {
      label: "Architecture graph available",
      met: graphUsed,
      weight: "high",
    },
    {
      label: "Jira connected",
      met: false,
      weight: "medium",
    },
    {
      label: "Confluence connected",
      met: false,
      weight: "medium",
    },
  ];
}

export function PlanningConfidencePanel({ confidence, result }: PlanningConfidencePanelProps) {
  const score = confidence.score ?? 0;
  const pct = Math.round(score * 100);
  const factors = deriveFactors(result);
  const missingHigh = factors.filter((f) => !f.met && f.weight === "high").length;
  const missingTotal = factors.filter((f) => !f.met).length;

  return (
    <Card title="Confidence Breakdown">
      <div className="flex flex-col gap-4">
        {/* Score gauge */}
        <div className="flex items-end gap-3">
          <span className={`text-3xl font-bold tabular-nums ${scoreColor(confidence.score)}`}>
            {pct}%
          </span>
          <div className="mb-1 flex-1">
            <div className="h-2 overflow-hidden rounded-full bg-surface-raised">
              <div
                className={`h-2 rounded-full transition-all duration-500 ${scoreBarColor(confidence.score)}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        </div>

        {/* Factors */}
        <ul className="flex flex-col gap-1.5">
          {factors.map((f) => (
            <li key={f.label} className="flex items-center gap-2">
              {f.met ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success-fg" aria-hidden="true" />
              ) : (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
              )}
              <span
                className={`text-xs ${f.met ? "text-fg-secondary" : f.weight === "high" ? "text-fg-muted" : "text-fg-muted"}`}
              >
                {f.label}
              </span>
              {!f.met && f.weight === "high" && (
                <span className="ml-auto shrink-0 rounded bg-danger-bg px-1 py-0.5 text-[10px] font-medium text-danger-fg">
                  missing
                </span>
              )}
            </li>
          ))}
        </ul>

        {/* Recommendation */}
        {missingTotal > 0 && (
          <div className="rounded-lg border border-info-line/20 bg-info-bg px-3 py-2">
            <p className="text-xs text-info-fg">
              {missingHigh > 0
                ? `Connect ${missingHigh} missing engineering system${missingHigh === 1 ? "" : "s"} to significantly improve confidence.`
                : "Connect Jira and Confluence to improve context quality."}
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
