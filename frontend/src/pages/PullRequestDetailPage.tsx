import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Card } from "../components/Card";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  getAiAnalysis,
  getDeterministicAnalysis,
  runAiAnalysis,
  runDeterministicAnalysis,
} from "../lib/api/analysis";
import { usePullRequestsData } from "../hooks/usePullRequestsData";
import type {
  AIAnalysis,
  AIAnalysisResult,
  ImpactedNode,
  PullRequestAnalysis,
  RiskLevel,
} from "../types/analysis";

async function orNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

const RISK_TONE: Record<RiskLevel, "danger" | "warning" | "success"> = {
  HIGH: "danger",
  MEDIUM: "warning",
  LOW: "success",
};

function ImpactedNodeList({ nodes, emptyLabel }: { nodes: ImpactedNode[]; emptyLabel: string }) {
  if (nodes.length === 0) {
    return <p className="text-xs text-slate-500">{emptyLabel}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-2">
      {nodes.map((node) => (
        <li
          key={node.id}
          className="rounded-md bg-slate-800/80 px-2 py-1 text-xs text-slate-300"
          title={node.node_type}
        >
          {node.name}
        </li>
      ))}
    </ul>
  );
}

function DeterministicPanel({ analysis }: { analysis: PullRequestAnalysis }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <RiskBadge level={analysis.risk.toLowerCase() as "high" | "medium" | "low"} />
        <span className="text-xs text-slate-500">
          <StatusBadge label={analysis.risk} tone={RISK_TONE[analysis.risk]} />
        </span>
      </div>

      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
          Directly impacted
        </p>
        <ImpactedNodeList
          nodes={analysis.directly_impacted_services}
          emptyLabel="No directly impacted components."
        />
      </div>

      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
          Indirectly impacted (cross-repository)
        </p>
        <ImpactedNodeList
          nodes={analysis.indirectly_impacted_services}
          emptyLabel="No cross-repository impact detected."
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">APIs</p>
          <ImpactedNodeList nodes={analysis.impacted_apis} emptyLabel="None." />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Kafka topics
          </p>
          <ImpactedNodeList nodes={analysis.impacted_topics} emptyLabel="None." />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Libraries
          </p>
          <ImpactedNodeList nodes={analysis.impacted_libraries} emptyLabel="None." />
        </div>
      </div>

      {analysis.dependency_paths.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Dependency paths
          </p>
          <ul className="flex flex-col gap-1">
            {analysis.dependency_paths.map((path, i) => (
              <li key={i} className="text-xs text-slate-400">
                {path.steps.map((step) => step.node_name).join(" → ")}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function AiAnalysisPanel({ ai }: { ai: AIAnalysis | AIAnalysisResult }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-slate-300">{ai.executive_summary}</p>

      {ai.breaking_changes.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Breaking changes
          </p>
          <ul className="flex flex-col gap-2">
            {ai.breaking_changes.map((bc, i) => (
              <li key={i} className="rounded-md bg-slate-800/60 p-2 text-xs">
                <span className="font-medium text-slate-200">{bc.component}</span> —{" "}
                {bc.description} <span className="text-slate-500">({bc.severity})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.migration_advice.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Migration advice
          </p>
          <ul className="flex flex-col gap-2">
            {ai.migration_advice.map((advice, i) => (
              <li key={i} className="rounded-md bg-slate-800/60 p-2 text-xs">
                <span className="font-medium text-slate-200">{advice.component}</span> —{" "}
                {advice.advice} <span className="text-slate-500">({advice.priority})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.suggested_reviewers.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Suggested reviewers
          </p>
          <ul className="flex flex-wrap gap-2">
            {ai.suggested_reviewers.map((reviewer, i) => (
              <li
                key={i}
                className="rounded-md bg-slate-800/80 px-2 py-1 text-xs text-slate-300"
                title={reviewer.reason}
              >
                {reviewer.reviewer}
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.regression_tests.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Suggested regression tests
          </p>
          <ul className="flex flex-col gap-2">
            {ai.regression_tests.map((test, i) => (
              <li key={i} className="rounded-md bg-slate-800/60 p-2 text-xs">
                <span className="font-medium text-slate-200">{test.component}</span> —{" "}
                {test.test_description}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-slate-500">
        Confidence:{" "}
        {Math.round(("confidence" in ai ? ai.confidence.score : ai.confidence_score) * 100)}%
      </p>
    </div>
  );
}

function ReleaseCoordinationPanel({
  plan,
}: {
  plan: AIAnalysisResult["release_coordination_plan"];
}) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-slate-500">
        Regenerated fresh on every run — not persisted. Only shown for the AI analysis you just ran.
      </p>

      {plan.deployment_order.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Deployment order
          </p>
          <ol className="flex flex-col gap-2">
            {plan.deployment_order.map((step) => (
              <li key={step.order} className="rounded-md bg-slate-800/60 p-2 text-xs">
                <span className="font-medium text-slate-200">
                  {step.order}. {step.repository}
                </span>{" "}
                — {step.action} <span className="text-slate-500">({step.reason})</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {plan.repositories_to_notify.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Repositories to notify
          </p>
          <ul className="flex flex-col gap-2">
            {plan.repositories_to_notify.map((entry, i) => (
              <li
                key={i}
                className="flex items-center gap-2 rounded-md bg-slate-800/60 p-2 text-xs"
              >
                <StatusBadge
                  label={entry.urgency}
                  tone={entry.urgency === "blocking" ? "danger" : "warning"}
                />
                <span className="font-medium text-slate-200">{entry.repository}</span>
                <span className="text-slate-500">{entry.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.rollout_strategy && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Rollout strategy
          </p>
          <p className="text-xs text-slate-300">{plan.rollout_strategy}</p>
        </div>
      )}

      {plan.backward_compatibility_advice && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Backward compatibility
          </p>
          <p className="text-xs text-slate-300">{plan.backward_compatibility_advice}</p>
        </div>
      )}

      {plan.communication_summary && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Communication summary
          </p>
          <p className="text-xs text-slate-300">{plan.communication_summary}</p>
        </div>
      )}

      {plan.rollout_risks.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Rollout risks
          </p>
          <ul className="list-inside list-disc text-xs text-slate-300">
            {plan.rollout_risks.map((risk, i) => (
              <li key={i}>{risk}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function PullRequestDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const { pullRequests } = usePullRequestsData();
  const pr = pullRequests.find((row) => row.id === id);

  const [deterministic, setDeterministic] = useState<PullRequestAnalysis | null>(null);
  const [aiAnalysis, setAiAnalysis] = useState<AIAnalysis | AIAnalysisResult | null>(null);
  const [releasePlan, setReleasePlan] = useState<
    AIAnalysisResult["release_coordination_plan"] | null
  >(null);
  const [isRunningDeterministic, setIsRunningDeterministic] = useState(false);
  const [isRunningAi, setIsRunningAi] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !id) {
      return;
    }
    let cancelled = false;
    (async () => {
      const [existingDeterministic, existingAi] = await Promise.all([
        orNull(getDeterministicAnalysis(token, id)),
        orNull(getAiAnalysis(token, id)),
      ]);
      if (!cancelled) {
        if (existingDeterministic) setDeterministic(existingDeterministic);
        if (existingAi) setAiAnalysis(existingAi);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, id]);

  async function handleRunDeterministic() {
    if (!token || !id) return;
    setIsRunningDeterministic(true);
    setError(null);
    try {
      setDeterministic(await runDeterministicAnalysis(token, id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run deterministic analysis.");
    } finally {
      setIsRunningDeterministic(false);
    }
  }

  async function handleRunAi() {
    if (!token || !id) return;
    setIsRunningAi(true);
    setError(null);
    try {
      const result = await runAiAnalysis(token, id);
      setAiAnalysis(result);
      setReleasePlan(result.release_coordination_plan);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run AI analysis.");
    } finally {
      setIsRunningAi(false);
    }
  }

  if (!pr) {
    return <p className="text-sm text-slate-500">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">{pr.title}</h2>
        <p className="mt-1 text-sm text-slate-400">
          <Link to={`/repositories/${pr.repositoryId}`} className="hover:underline">
            {pr.repositoryFullName}
          </Link>{" "}
          · #{pr.number} by {pr.authorLogin}
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card
        title="Deterministic analysis"
        description="Graph-based impact analysis - no AI involved"
        action={
          <button
            type="button"
            onClick={() => void handleRunDeterministic()}
            disabled={isRunningDeterministic}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunningDeterministic
              ? "Running…"
              : deterministic
                ? "Re-run analysis"
                : "Run analysis"}
          </button>
        }
      >
        {deterministic ? (
          <DeterministicPanel analysis={deterministic} />
        ) : (
          <p className="text-sm text-slate-500">Not analyzed yet.</p>
        )}
      </Card>

      <Card
        title="AI analysis"
        description="Executive summary, breaking changes, migration advice, and suggested reviewers"
        action={
          <button
            type="button"
            onClick={() => void handleRunAi()}
            disabled={isRunningAi}
            className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunningAi ? "Running…" : aiAnalysis ? "Re-run AI analysis" : "Run AI analysis"}
          </button>
        }
      >
        {aiAnalysis ? (
          <AiAnalysisPanel ai={aiAnalysis} />
        ) : (
          <p className="text-sm text-slate-500">Not analyzed yet.</p>
        )}
      </Card>

      <Card
        title="Release Coordination Plan"
        description="Deployment order and cross-repository notifications - AI-enriched, ephemeral"
      >
        {releasePlan ? (
          <ReleaseCoordinationPanel plan={releasePlan} />
        ) : (
          <p className="text-sm text-slate-500">
            Run AI analysis above to generate a release coordination plan.
          </p>
        )}
      </Card>
    </div>
  );
}
