import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ListChecks } from "lucide-react";
import { Card } from "../components/Card";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { createEngineeringTask } from "../lib/api/engineeringTasks";

/**
 * Minimal Engineering Task creation form — Phase 7.2. Submits to the
 * existing, unmodified `POST /engineering-tasks`. Two fields only: the
 * Goal itself, and its postconditions (required by that existing API,
 * min length 1 — not an addition invented for this form). No workflow
 * type, no extra configuration.
 */
export function NewEngineeringTaskPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [description, setDescription] = useState("");
  const [postconditionsText, setPostconditionsText] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const postconditions = postconditionsText
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const canSubmit = description.trim().length > 0 && postconditions.length > 0;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit || !token) return;

    setIsSubmitting(true);
    setError(null);
    try {
      const task = await createEngineeringTask(token, {
        description: description.trim(),
        postconditions,
      });
      navigate(`/engineering-tasks/${task.task_id}`);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to create this Engineering Task.",
      );
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-accent-bg p-2 ring-1 ring-inset ring-accent-line/30">
          <ListChecks className="h-5 w-5 text-accent-fg" aria-hidden="true" />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-fg">New Engineering Task</h1>
          <p className="text-sm text-fg-muted">
            GraphForge plans this Goal, executes it, and independently verifies the result.
          </p>
        </div>
      </div>

      <Card>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {error && (
            <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
              {error}
            </div>
          )}

          <div>
            <label htmlFor="goal-description" className="block text-sm font-medium text-fg-secondary">
              What's the engineering objective?
            </label>
            <textarea
              id="goal-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder="Find repositories containing payment processing code"
              className="mt-1.5 block w-full rounded-lg border border-line-muted bg-canvas px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus-ring"
            />
          </div>

          <div>
            <label
              htmlFor="goal-postconditions"
              className="block text-sm font-medium text-fg-secondary"
            >
              How will you know it's done? (one per line)
            </label>
            <textarea
              id="goal-postconditions"
              value={postconditionsText}
              onChange={(e) => setPostconditionsText(e.target.value)}
              rows={3}
              placeholder="At least one repository is identified"
              className="mt-1.5 block w-full rounded-lg border border-line-muted bg-canvas px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus-ring"
            />
            <p className="mt-1 text-xs text-fg-muted">
              Checkable postconditions Independent Verification will evaluate — at least one is
              required.
            </p>
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!canSubmit || isSubmitting}
              className="focus-ring inline-flex items-center rounded-lg bg-accent-solid px-4 py-2 text-sm font-semibold text-accent-on-solid shadow-xs transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSubmitting ? "Creating…" : "Create Engineering Task"}
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}
