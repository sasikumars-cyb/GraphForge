import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { listWorkflows } from "../../lib/api/workflows";
import { PipelineGraph } from "./PipelineGraph";

/**
 * Workflows actively running right now — the second half of "what's my
 * work state", alongside WaitingOnYouPanel. Reuses PipelineGraph exactly
 * as the workflow detail page renders it (same icons, same colors, same
 * "running" shimmer) so a reader who's learned that visual language once
 * doesn't have to learn a second one here — this is a preview of the same
 * thing, not a different thing that happens to also show stage status.
 */
export function InFlightWorkflowsPanel() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["workflows-waiting", "in_progress"],
    queryFn: ({ signal }) =>
      listWorkflows(token as string, { status: "in_progress", page_size: 5 }, signal),
    enabled: token !== null,
  });

  if (query.isPending || !query.data || query.data.items.length === 0) return null;

  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold text-fg">In flight</h2>
      <div className="flex flex-col gap-3">
        {query.data.items.map((workflow) => (
          <div
            key={workflow.workflow_id}
            className="rounded-xl border border-line-muted bg-surface p-4"
          >
            <div className="mb-3 flex items-center justify-between gap-3">
              <Link
                to={`/workflows/${workflow.workflow_id}`}
                className="truncate text-sm font-medium text-fg hover:text-accent-fg hover:underline"
              >
                {workflow.title}
              </Link>
            </div>
            {/* Read-only preview: clicking a stage still takes you to the
                real workflow page (where its run detail actually lives)
                rather than trying to reproduce run selection here. */}
            <PipelineGraph
              stages={workflow.stages}
              selectedRunId={null}
              onSelectStage={() => navigate(`/workflows/${workflow.workflow_id}`)}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
