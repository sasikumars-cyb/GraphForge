import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { listWorkflows } from "../../lib/api/workflows";
import { formatRelativeTime } from "../../lib/formatDate";
import { PipelineGraph } from "../workflow/PipelineGraph";

/**
 * "What is GraphForge working on right now?" — evolved from the former
 * InFlightWorkflowsPanel, answerable without opening Runs. Reuses
 * PipelineGraph exactly as the workflow detail page renders it (same
 * icons, same colors, same "running" shimmer) so a reader who's learned
 * that visual language once doesn't have to learn a second one here —
 * this is a compact preview of the same thing, not a different thing
 * that happens to also show stage status. Read-only: every interaction
 * routes to the real workflow detail page, which remains the execution
 * layer. This is the summary layer.
 */
export function ActiveMissionsPanel() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["workflows-waiting", "in_progress"],
    queryFn: ({ signal }) =>
      listWorkflows(token as string, { status: "in_progress", page_size: 5 }, signal),
    enabled: token !== null,
  });

  if (query.isPending) {
    return (
      <section aria-label="Active missions" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">Active missions</h2>
        <div className="h-28 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  const missions = query.data?.items ?? [];

  return (
    <section aria-label="Active missions" className="flex flex-col gap-2">
      <h2
        aria-label={
          missions.length > 0
            ? `${missions.length} active mission${missions.length === 1 ? "" : "s"}`
            : "Active missions"
        }
        className="flex items-center gap-2 text-sm font-semibold text-fg"
      >
        {missions.length > 0 && (
          <span
            aria-hidden="true"
            className="flex h-5 w-5 items-center justify-center rounded-full bg-info-solid text-[11px] font-bold text-info-on-solid"
          >
            {missions.length}
          </span>
        )}
        Active missions
      </h2>
      {missions.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-line px-6 py-8 text-center">
          <p className="text-sm font-medium text-fg-secondary">Nothing in progress</p>
          <p className="text-xs text-fg-muted">
            Start one from{" "}
            <Link to="/workflows/new" className="text-accent-fg hover:underline">
              New Workflow
            </Link>
            .
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {missions.map((workflow) => (
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
                <span className="shrink-0 text-xs text-fg-subtle">
                  started {formatRelativeTime(workflow.created_at)}
                </span>
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
      )}
    </section>
  );
}
