import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { Card } from "../Card";
import { useAuth } from "../../app/auth-context";
import { getSystemStatus, type ConnectionStatus } from "../../lib/api/system";
import type { Confidence, PlanningResult } from "../../types/agent";

interface PlanningConfidencePanelProps {
  confidence: Confidence;
  result: PlanningResult | undefined;
}

interface Factor {
  label: string;
  met: boolean;
  weight: "high" | "medium";
  /** Shown when unmet — what the user can actually do about it. */
  remedy?: string;
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

/**
 * Which context sources this plan actually had.
 *
 * Every factor here is derived from something observable — the run's own
 * result, or the live `/system/status` connection list. Two factors used to
 * be hardcoded (`"Jira connected": false` and `"Confluence connected":
 * false`) and were therefore reported as missing even in a workspace where
 * Jira was connected and working; a third (`"Business requirements
 * provided"`) was hardcoded `true` and so could never convey anything. A
 * panel whose job is to justify a number cannot itself assert things it has
 * not checked, so those are gone.
 *
 * An integration that this deployment doesn't expose at all (Confluence is
 * still roadmap — see the README) yields no row rather than a permanently
 * unmet one: "not built yet" is not a gap the reader can close.
 */
function deriveFactors(
  result: PlanningResult | undefined,
  connections: ConnectionStatus[] | undefined,
): Factor[] {
  const connection = (name: string) =>
    connections?.find((c) => c.name.toLowerCase() === name.toLowerCase());
  const isConnected = (name: string) => connection(name)?.status === "connected";

  const factors: Factor[] = [
    {
      label: "Implementation plan generated",
      met: (result?.implementation_steps?.length ?? 0) > 0,
      weight: "high",
    },
    {
      label: "Repositories consulted",
      met: (result?.repositories_consulted?.length ?? 0) > 0,
      weight: "high",
      remedy: "Index a repository so planning has code to reason about.",
    },
    {
      label: "Architecture graph used",
      met: result?.graph_context_used ?? false,
      weight: "high",
      remedy: "Index a repository to build its architecture graph.",
    },
    {
      label: "Architecture layers defined",
      met: (result?.architecture_layers?.length ?? 0) > 0,
      weight: "medium",
    },
  ];

  // Only offer an integration as a factor if this deployment knows about it.
  for (const name of ["Jira", "Confluence"]) {
    if (!connection(name)) continue;
    factors.push({
      label: `${name} connected`,
      met: isConnected(name),
      weight: "medium",
      remedy: `Connect ${name} in Settings → Integrations for richer context.`,
    });
  }

  return factors;
}

export function PlanningConfidencePanel({ confidence, result }: PlanningConfidencePanelProps) {
  const { token } = useAuth();
  // Degrades to "no connection rows" if this fails — the result-derived
  // factors above are still fully valid without it, and a status-endpoint
  // hiccup should not blank out the panel.
  const systemQuery = useQuery({
    queryKey: ["system-status"],
    queryFn: ({ signal }) => getSystemStatus(token as string, signal),
    enabled: token !== null,
  });

  const score = confidence.score ?? 0;
  const pct = Math.round(score * 100);
  const factors = deriveFactors(result, systemQuery.data?.connections);
  const unmet = factors.filter((f) => !f.met);
  const remedies = [...new Set(unmet.map((f) => f.remedy).filter(Boolean))] as string[];

  return (
    <Card
      title="Confidence"
      description="The score is the planning agent's own. Below is the context it had to work with."
    >
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

        {confidence.reasoning && <p className="text-xs text-fg-muted">{confidence.reasoning}</p>}

        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Context available
          </p>
          <ul className="flex flex-col gap-1.5">
            {factors.map((f) => (
              <li key={f.label} className="flex items-center gap-2">
                {f.met ? (
                  <CheckCircle2
                    className="h-3.5 w-3.5 shrink-0 text-success-fg"
                    aria-hidden="true"
                  />
                ) : (
                  <XCircle className="h-3.5 w-3.5 shrink-0 text-fg-subtle" aria-hidden="true" />
                )}
                <span className={`text-xs ${f.met ? "text-fg-secondary" : "text-fg-muted"}`}>
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
        </div>

        {remedies.length > 0 && (
          <div className="rounded-lg border border-info-line/20 bg-info-bg px-3 py-2">
            <ul className="flex flex-col gap-1 text-xs text-info-fg">
              {remedies.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}
