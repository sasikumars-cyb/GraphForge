import { useState } from "react";
import { Check, GitBranch, Loader2 } from "lucide-react";
import type { ContextDiscoveryResult, RepositoryCandidate } from "../../types/agent";
import { overrideStageResult } from "../../lib/api/workflows";
import { useAuth } from "../../app/auth-context";

interface RepositorySelectorProps {
  workflowId: string;
  result: ContextDiscoveryResult;
  /** A human's saved selection, if any — same override mechanism
   * `ContextExplorerPanel` uses for `graph_context_text`, keyed on the
   * canonical `repositories` field (ADR 0010 §2). */
  humanOverride?: Record<string, unknown> | null;
  onOverridden: () => void;
}

/** ADR 0010 §2 / invariant I6: the only field this component ever reads or
 * writes is the canonical `repositories` — never `explicit_repositories`/
 * `suggested_repositories`/`selected_repositories` (those are read-only
 * projections the backend computes once, at write time, and never
 * recomputes after an override changes `repositories`). Merging the
 * human override here mirrors exactly what `get_stage_result()` already
 * does server-side for every other reader. */
function effectiveRepositories(
  result: ContextDiscoveryResult,
  humanOverride: Record<string, unknown> | null | undefined,
): RepositoryCandidate[] {
  const overridden = humanOverride?.repositories;
  if (Array.isArray(overridden)) return overridden as RepositoryCandidate[];
  return result.repositories ?? [];
}

/** "Select repositories for this work" — replaces the old single-answer
 * "Pick one" for the common case. Explicitly-referenced repositories are
 * always pre-checked (never blocking); suggested ones, each with a real
 * reason from the knowledge graph, start unchecked. The human can change
 * either before continuing — saved as a `context_discovery` stage override
 * on `repositories` itself, same mechanism the graph-context correction
 * above already uses, so Planning/Development/Testing (via
 * `get_stage_result()`) see the correction automatically. */
export function RepositorySelector({
  workflowId,
  result,
  humanOverride,
  onOverridden,
}: RepositorySelectorProps) {
  const { token } = useAuth();
  const repositories = effectiveRepositories(result, humanOverride);
  const explicit = repositories.filter((r) => r.source === "explicit");
  const suggested = repositories.filter((r) => r.source === "suggested");
  const effectiveSelectedNames = repositories.filter((r) => r.selected).map((r) => r.name);

  const [selected, setSelected] = useState<Set<string>>(() => new Set(effectiveSelectedNames));
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (repositories.length === 0) return null;

  const dirty =
    selected.size !== effectiveSelectedNames.length ||
    effectiveSelectedNames.some((name) => !selected.has(name));

  const toggle = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handleSave = async () => {
    if (!token) return;
    setIsSaving(true);
    setError(null);
    try {
      const updated = repositories.map((r) => ({ ...r, selected: selected.has(r.name) }));
      await overrideStageResult(token, workflowId, "context_discovery", {
        override: { repositories: updated },
      });
      onOverridden();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save repository selection.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line-muted bg-surface-raised px-3 py-3">
      <div className="flex items-start gap-2">
        <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
        <div className="flex flex-col gap-0.5">
          <p className="text-xs font-semibold text-fg-secondary">
            {explicit.length > 0
              ? "I found these repositories explicitly referenced in the Jira."
              : "Select repositories for this work"}
          </p>
          <p className="text-xs text-fg-muted">
            Every selected repository is searched and planned across — not just the top-ranked
            one. Change the selection below if it's wrong.
          </p>
        </div>
      </div>

      {explicit.length > 0 && (
        <div className="flex flex-col gap-1.5 pl-6">
          <p className="text-xs font-medium text-fg-subtle">Explicitly referenced in Jira</p>
          {explicit.map((repo) => (
            <RepositoryCheckbox
              key={repo.name}
              repo={repo}
              checked={selected.has(repo.name)}
              onToggle={() => toggle(repo.name)}
              disabled={isSaving}
            />
          ))}
        </div>
      )}

      {suggested.length > 0 && (
        <div className="flex flex-col gap-1.5 pl-6">
          <p className="text-xs font-medium text-fg-subtle">Suggested by knowledge graph</p>
          {suggested.map((repo) => (
            <RepositoryCheckbox
              key={repo.name}
              repo={repo}
              checked={selected.has(repo.name)}
              onToggle={() => toggle(repo.name)}
              disabled={isSaving}
            />
          ))}
        </div>
      )}

      {error && <p className="pl-6 text-xs text-danger-fg">{error}</p>}

      {dirty && (
        <div className="pl-6">
          <button
            type="button"
            onClick={handleSave}
            disabled={isSaving || selected.size === 0}
            className="focus-ring inline-flex items-center gap-1.5 rounded-lg bg-accent-solid px-3 py-1.5 text-xs font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            {isSaving ? "Saving…" : "Save selection"}
          </button>
        </div>
      )}
    </div>
  );
}

function RepositoryCheckbox({
  repo,
  checked,
  onToggle,
  disabled,
}: {
  repo: RepositoryCandidate;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-xs">
      <span
        role="checkbox"
        aria-checked={checked}
        aria-disabled={disabled}
        onClick={disabled ? undefined : onToggle}
        onKeyDown={(e) => {
          if (disabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        tabIndex={disabled ? -1 : 0}
        className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border ${
          checked
            ? "border-accent-line bg-accent-solid text-accent-on-solid"
            : "border-line bg-surface"
        } ${disabled ? "opacity-50" : ""}`}
      >
        {checked && <Check className="h-3 w-3" aria-hidden="true" />}
      </span>
      <span className="flex flex-col">
        <span className="flex items-center gap-1.5">
          <span className="font-mono font-medium text-fg-secondary">{repo.name}</span>
          {repo.confidence === "heuristic" && (
            <span
              className="rounded bg-warning-bg px-1 py-0.5 text-[10px] font-medium text-warning-fg"
              title="A heuristic match (e.g. a dependency name) rather than a structural one (a Feign call or shared Kafka topic) — worth a closer look before relying on it."
            >
              possible match
            </span>
          )}
        </span>
        {repo.reason && (
          <span className="text-fg-subtle">
            Reason: <span className="text-fg-muted">{repo.reason}</span>
          </span>
        )}
      </span>
    </label>
  );
}
