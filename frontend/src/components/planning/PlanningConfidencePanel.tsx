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
  if (score === null) return "text-slate-400";
  if (score >= 0.8) return "text-emerald-400";
  if (score >= 0.5) return "text-amber-400";
  return "text-rose-400";
}

function scoreBarColor(score: number | null): string {
  if (score === null) return "bg-slate-600";
  if (score >= 0.8) return "bg-emerald-500";
  if (score >= 0.5) return "bg-amber-500";
  return "bg-rose-500";
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
            <div className="h-2 overflow-hidden rounded-full bg-slate-800">
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
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden="true" />
              ) : (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-slate-600" aria-hidden="true" />
              )}
              <span
                className={`text-xs ${f.met ? "text-slate-300" : f.weight === "high" ? "text-slate-400" : "text-slate-500"}`}
              >
                {f.label}
              </span>
              {!f.met && f.weight === "high" && (
                <span className="ml-auto shrink-0 rounded bg-rose-500/10 px-1 py-0.5 text-[10px] font-medium text-rose-400">
                  missing
                </span>
              )}
            </li>
          ))}
        </ul>

        {/* Recommendation */}
        {missingTotal > 0 && (
          <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 px-3 py-2">
            <p className="text-xs text-sky-300">
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
