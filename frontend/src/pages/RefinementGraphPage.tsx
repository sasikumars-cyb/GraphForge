import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { WorkItemGraph } from "../components/refinement/WorkItemGraph";
import { TicketIntelligencePanel } from "../components/refinement/TicketIntelligencePanel";
import { ProvenanceTag } from "../components/intelligence/ProvenanceTag";
import { useAuth } from "../app/auth-context";
import { getConversation } from "../lib/api/conversations";
import type { ConversationMessage, RefinementPlan } from "../types/conversation";

function latestPlan(messages: ConversationMessage[]): RefinementPlan | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const plan = messages[i].payload?.refinement;
    if (plan) return plan;
  }
  return null;
}

function MetricChip({ label }: { label: string }) {
  return (
    <span className="rounded-full bg-neutral-bg px-2.5 py-1 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line">
      {label}
    </span>
  );
}

/**
 * The interactive work-item dependency map — Refinement Planner's
 * conversation, visualized. Reads the LATEST refinement plan off
 * whichever assistant turn most recently set one (the same
 * `ConversationMessage.payload.refinement` the chat itself renders a
 * compact summary of), so this always shows exactly what the
 * conversation currently believes, never a frozen export. Deep-linked
 * from a "Show dependencies" action — see `_refinement_actions` on the
 * backend.
 */
export function RefinementGraphPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const { token } = useAuth();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: ({ signal }) => getConversation(token as string, conversationId as string, signal),
    enabled: token !== null && Boolean(conversationId),
  });

  const plan = query.data ? latestPlan(query.data.messages) : null;
  const selected = plan?.work_items.find((w) => w.id === selectedId) ?? null;

  const metrics = plan
    ? {
        total: plan.work_items.length,
        dependencies: plan.edges.length,
        blocking: plan.edges.filter((e) => e.relationship === "blocks").length,
        spikes: plan.work_items.filter((w) => w.type === "spike").length,
        parallelizable: plan.parallelizable_ids.length,
      }
    : null;

  return (
    <div className="flex h-full flex-col gap-4">
      <Link
        to={conversationId ? `/workspace/refinement-planner?resume=${conversationId}` : "/workspace/refinement-planner"}
        className="flex w-fit items-center gap-1.5 text-sm text-fg-muted transition-colors hover:text-fg-secondary"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to conversation
      </Link>

      {query.isLoading && <p className="text-sm text-fg-muted">Loading…</p>}

      {!query.isLoading && (!plan || plan.work_items.length === 0) && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-sm text-fg-muted">
            No dependency graph yet — nothing has been proposed in this investigation to show.
          </p>
          <Link
            to="/workspace/refinement-planner"
            className="text-sm font-medium text-accent-fg hover:underline"
          >
            Go to Refinement Planner
          </Link>
        </div>
      )}

      {plan && metrics && plan.work_items.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <MetricChip label={`${metrics.total} work items`} />
            <MetricChip label={`${metrics.dependencies} dependencies`} />
            <MetricChip label={`${metrics.blocking} blocking`} />
            <MetricChip label={`${metrics.spikes} spike${metrics.spikes === 1 ? "" : "s"}`} />
            <MetricChip label={`${metrics.parallelizable} parallelizable`} />
            <ProvenanceTag kind="derived" label="Derived from the current plan" />
          </div>

          {plan.critical_paths.length > 0 && (
            <p className="text-xs text-fg-muted">
              <span className="font-semibold text-fg-secondary">
                {plan.critical_paths.length > 1 ? "Key dependency paths: " : "Critical path: "}
              </span>
              {plan.critical_paths.map((path) => path.join(" → ")).join("  ·  ")}
            </p>
          )}

          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[1fr_280px]">
            {/* A fixed height, not just a minimum: the app shell's own
                <main> has no bounded height to hand a `flex-1`/`h-full`
                descendant here, so without an explicit floor ReactFlow's
                fitView was measuring a ~420px canvas and zooming node
                labels down to the point of being unreadable at a normal
                viewing distance — presentation-critical, not cosmetic. */}
            <div className="h-[640px] overflow-hidden rounded-xl border border-line-muted bg-surface lg:h-[calc(100vh-260px)]">
              <WorkItemGraph plan={plan} selectedId={selectedId} onSelect={setSelectedId} />
            </div>
            <TicketIntelligencePanel plan={plan} item={selected} onSelect={setSelectedId} />
          </div>
        </>
      )}
    </div>
  );
}
