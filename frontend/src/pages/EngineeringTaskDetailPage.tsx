import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Card } from "../components/Card";
import { StatusBadge, type StatusTone } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { getEngineeringTask } from "../lib/api/engineeringTasks";
import { formatRelativeTime } from "../lib/formatDate";
import type { EngineeringTask, EngineeringTaskObservation } from "../types/engineeringTask";

/**
 * Read-only viewer for one Engineering Task's materialized Engineering
 * State (Phase 7.1 — the minimal visibility slice).
 *
 * Strictly a viewer: this page calls exactly one API function
 * (`getEngineeringTask`, a GET) and renders its result. It imports
 * nothing from `lib/api/workflows.ts` and contains no button, form, or
 * action that could append an Engineering Event, call ControlPlane,
 * call ReasoningPlane, execute a Tool, modify a Workspace, approve
 * anything, or trigger Replan — there is no write path here at all.
 * Creation remains API-only for this increment (`POST
 * /api/v1/engineering-tasks`, via curl/pytest, not this UI).
 */

const CLASSIFICATION_TONE: Record<string, StatusTone> = {
  expected: "success",
  anomaly: "warning",
  contradiction: "danger",
  uncertain_outcome: "warning",
};

function classificationTone(classification: string | null): StatusTone {
  if (classification === null) return "neutral";
  return CLASSIFICATION_TONE[classification] ?? "neutral";
}

function ObservationCard({
  title,
  observation,
}: {
  title: string;
  observation: EngineeringTaskObservation;
}) {
  return (
    <Card title={title}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-fg-muted">Classification</dt>
        <dd>
          {observation.classification ? (
            <StatusBadge
              label={observation.classification}
              tone={classificationTone(observation.classification)}
            />
          ) : (
            <span className="text-fg-muted">—</span>
          )}
        </dd>
        <dt className="text-fg-muted">Outcome</dt>
        <dd>{observation.outcome ?? "—"}</dd>
        <dt className="text-fg-muted">Success</dt>
        <dd>{observation.success === null ? "—" : observation.success ? "Yes" : "No"}</dd>
        <dt className="text-fg-muted">Actor</dt>
        <dd className="font-mono text-xs">{observation.actor ?? "—"}</dd>
      </dl>
    </Card>
  );
}

export function EngineeringTaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const { token } = useAuth();
  const [task, setTask] = useState<EngineeringTask | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !taskId) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    async function load() {
      setIsLoading(true);
      setError(null);
      setNotFound(false);
      try {
        const result = await getEngineeringTask(token!, taskId!, controller.signal);
        if (!cancelled) {
          setTask(result);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Failed to load this engineering task.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [token, taskId]);

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="text-sm text-fg-muted">Loading engineering task…</p>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="p-6">
        <Card title="Not found">
          <p className="text-sm text-fg-muted">
            No engineering task exists for id <span className="font-mono">{taskId}</span>.
          </p>
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <Card title="Error">
          <p className="text-sm text-danger-fg">{error}</p>
        </Card>
      </div>
    );
  }

  if (!task) {
    return null;
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      <div>
        <h1 className="font-display text-lg font-semibold text-fg">Engineering Task</h1>
        <p className="mt-1 text-xs text-fg-muted">
          <span className="font-mono">{task.task_id}</span> · created{" "}
          {formatRelativeTime(task.created_at)}
        </p>
      </div>

      <Card title="Goal">
        <p className="text-sm text-fg">{task.goal.description}</p>
        {task.goal.postconditions.length > 0 && (
          <ul className="mt-2 list-inside list-disc text-sm text-fg-muted">
            {task.goal.postconditions.map((postcondition) => (
              <li key={postcondition}>{postcondition}</li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Plan Step">
        {task.plan_step ? (
          <div className="flex flex-col gap-2 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-fg">{task.plan_step.description}</span>
              {task.plan_step.invalidated && <StatusBadge label="invalidated" tone="danger" />}
            </div>
            <p className="text-xs text-fg-muted">
              Postcondition: {task.plan_step.postcondition}
            </p>
          </div>
        ) : (
          <p className="text-sm text-fg-muted">No PlanStep recorded yet.</p>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <ObservationCard title="Execution" observation={task.generator_observation} />
        <ObservationCard title="Verification" observation={task.verifier_observation} />
      </div>
    </div>
  );
}
