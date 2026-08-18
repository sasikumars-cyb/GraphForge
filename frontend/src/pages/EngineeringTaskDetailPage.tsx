import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { getEngineeringTask } from "../lib/api/engineeringTasks";
import { classificationPresentation } from "../lib/engineeringTaskClassification";
import { formatRelativeTime } from "../lib/formatDate";
import type { EngineeringTask, EngineeringTaskObservation } from "../types/engineeringTask";

/**
 * Read-only viewer for one Engineering Task's materialized Engineering
 * State (Phase 7.1's visibility slice, productized in Phase 7.2).
 *
 * Strictly a viewer: this page calls exactly one API function
 * (`getEngineeringTask`, a GET) and renders its result. It imports
 * nothing from `lib/api/workflows.ts` and contains no button, form, or
 * action that could append an Engineering Event, call ControlPlane,
 * call ReasoningPlane, execute a Tool, modify a Workspace, approve
 * anything, or trigger Replan — there is no write path here at all.
 * Creation happens on a genuinely separate page (`/engineering-tasks/new`).
 */

function ObservationCard({
  title,
  observation,
}: {
  title: string;
  observation: EngineeringTaskObservation;
}) {
  const presentation = classificationPresentation(observation.classification, observation.outcome);
  return (
    <Card title={title}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <dt className="text-fg-muted">Classification</dt>
        <dd>
          <StatusBadge label={presentation.label} tone={presentation.tone} />
        </dd>
        <dt className="text-fg-muted">Capability</dt>
        <dd className="font-mono text-xs">{observation.capability ?? "—"}</dd>
        <dt className="text-fg-muted">Outcome</dt>
        <dd>{observation.outcome ?? "—"}</dd>
        <dt className="text-fg-muted">Success</dt>
        <dd>{observation.success === null ? "—" : observation.success ? "Yes" : "No"}</dd>
        <dt className="text-fg-muted">Actor</dt>
        <dd className="font-mono text-xs">
          {observation.actor ?? "—"}
          {observation.actor === "control_plane_verifier" && (
            <span className="ml-1.5 font-sans text-xs text-fg-muted">
              (automated verification, not a human reviewer)
            </span>
          )}
        </dd>
      </dl>
      {presentation.explanation && (
        <p className="mt-3 border-t border-line-muted pt-3 text-xs text-fg-muted">
          {presentation.explanation}
        </p>
      )}
      {/* Phase 8 — Observation/Evidence Detail Surfacing: the actual
          Tool-reported result/reason, already durable in Engineering
          State, redacted server-side before this ever renders. This is
          the diagnostic content the Phase 8 Design Audit found missing —
          "Anomaly" alone told the user nothing about WHY. */}
      {observation.summary && (
        <div className="mt-3 border-t border-line-muted pt-3">
          <p className="text-xs font-medium text-fg-secondary">Result summary</p>
          <p className="mt-0.5 text-xs whitespace-pre-wrap text-fg-muted">{observation.summary}</p>
        </div>
      )}
      {observation.error && (
        <div className="mt-3 border-t border-line-muted pt-3">
          <p className="text-xs font-medium text-danger-fg">Reported error</p>
          <p className="mt-0.5 text-xs whitespace-pre-wrap text-danger-fg">{observation.error}</p>
        </div>
      )}
    </Card>
  );
}

export function EngineeringTaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const { token } = useAuth();
  const [task, setTask] = useState<EngineeringTask | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [invalidId, setInvalidId] = useState(false);
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
      setInvalidId(false);
      try {
        const result = await getEngineeringTask(token!, taskId!, controller.signal);
        if (!cancelled) {
          setTask(result);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError && err.code === "validation_error") {
          // The backend's validation-error message ("Request validation
          // failed.") is a correct-but-generic string shared by every
          // endpoint's request validation, not written for a human
          // reading this specific page — replaced with a message that
          // says what's actually wrong here.
          setInvalidId(true);
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

  const BackLink = () => (
    <Link
      to="/engineering-tasks"
      className="focus-ring inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-fg-secondary"
    >
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
      Back to Engineering Tasks
    </Link>
  );

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <BackLink />
        <p className="text-sm text-fg-muted">Loading Engineering Task…</p>
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <BackLink />
        <Card title="Not found">
          <p className="text-sm text-fg-muted">
            No Engineering Task exists for id <span className="font-mono">{taskId}</span>.
          </p>
        </Card>
      </div>
    );
  }

  if (invalidId) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <BackLink />
        <Card title="Invalid task link">
          <p className="text-sm text-fg-muted">
            That doesn't look like a valid Engineering Task link. Double-check the URL, or go back
            to the list and open a task from there.
          </p>
        </Card>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-4 p-6">
        <BackLink />
        <Card title="Error">
          <p className="text-sm text-danger-fg">{error}</p>
        </Card>
      </div>
    );
  }

  if (!task) {
    return null;
  }

  const overall = classificationPresentation(
    task.verifier_observation.classification,
    task.verifier_observation.outcome,
  );

  return (
    <div className="flex flex-col gap-4 p-6">
      <BackLink />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-lg font-semibold text-fg">Engineering Task</h1>
          <p className="mt-1 text-xs text-fg-muted">
            <span className="font-mono">{task.task_id}</span> · created{" "}
            {formatRelativeTime(task.created_at)}
          </p>
        </div>
        <StatusBadge label={overall.label} tone={overall.tone} />
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
