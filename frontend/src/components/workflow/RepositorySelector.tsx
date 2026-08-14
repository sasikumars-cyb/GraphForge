import { useState } from "react";
import { Check, GitBranch, Loader2, Plus } from "lucide-react";
import type { ContextDiscoveryResult, RepositoryCandidate } from "../../types/agent";
import { overrideStageResult } from "../../lib/api/workflows";
import { useAuth } from "../../app/auth-context";

// The backend's own generic ranking fallback (see
// `app.context_pipeline.reasoning.capabilities` — used only when no more
// specific structural/heuristic signal exists) — a fixed string, never
// ticket content, so translating it here is safe. A repository with a
// *specific* reason ("Shares Kafka topic 'orders-created' with etl-core.")
// keeps that reason untouched: it's more informative than any generic
// phrase, and rewriting it would throw away real signal.
const GENERIC_RANKING_REASON = "Ranks closely against this request's terms.";

function displayReason(reason: string): string {
  return reason === GENERIC_RANKING_REASON
    ? "Recommended because it appears most relevant to this request."
    : reason;
}

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
 * either before continuing, and can name a repository GraphForge didn't
 * find at all — saved as a `context_discovery` stage override on
 * `repositories` itself, same mechanism the graph-context correction
 * above already uses, so Planning/Development/Testing (via
 * `get_stage_result()`) see the correction automatically. */
export function RepositorySelector({
  workflowId,
  result,
  humanOverride,
  onOverridden,
}: RepositorySelectorProps) {
  const { token } = useAuth();
  const baseRepositories = effectiveRepositories(result, humanOverride);
  const [added, setAdded] = useState<RepositoryCandidate[]>([]);
  const repositories = [...baseRepositories, ...added];
  const explicit = repositories.filter((r) => r.source === "explicit");
  const suggested = repositories.filter((r) => r.source === "suggested");
  const effectiveSelectedNames = repositories.filter((r) => r.selected).map((r) => r.name);

  const [selected, setSelected] = useState<Set<string>>(() => new Set(effectiveSelectedNames));
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualName, setManualName] = useState("");
  const [showManualInput, setShowManualInput] = useState(false);

  const dirty =
    added.length > 0 ||
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

  const handleAddManual = () => {
    const name = manualName.trim();
    if (!name || repositories.some((r) => r.name === name)) {
      setManualName("");
      return;
    }
    setAdded((prev) => [
      ...prev,
      { name, source: "explicit", selected: true, reason: "Added manually." },
    ]);
    setSelected((prev) => new Set(prev).add(name));
    setManualName("");
    setShowManualInput(false);
  };

  const handleSave = async () => {
    if (!token) return;
    setIsSaving(true);
    setError(null);
    try {
      const updated = repositories.map((r) => ({ ...r, selected: selected.has(r.name) }));
      await overrideStageResult(token, workflowId, "context_discovery", {
        override: { repositories: updated },
        rerun: true,
      });
      onOverridden();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save repository selection.");
    } finally {
      setIsSaving(false);
    }
  };

  // GraphForge found nothing to suggest at all — still not a dead end.
  // A user who knows which repository this belongs to can name it, rather
  // than being stuck choosing only among what the investigation happened
  // to find.
  if (repositories.length === 0) {
    return (
      <div className="flex flex-col gap-2.5 rounded-lg border border-line-muted bg-surface-raised px-3 py-3">
        <div className="flex items-start gap-2">
          <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
          <div className="flex flex-col gap-0.5">
            <p className="text-xs font-semibold text-fg-secondary">
              GraphForge didn&apos;t confidently identify a repository for this work.
            </p>
            <p className="text-xs text-fg-muted">
              If you know which one this belongs to, add it below — Planning will search and plan
              across whatever you select here.
            </p>
          </div>
        </div>
        <ManualAddControl
          value={manualName}
          onChange={setManualName}
          onAdd={handleAddManual}
          show
        />
        {error && <p className="pl-6 text-xs text-danger-fg">{error}</p>}
        {dirty && (
          <div className="pl-6">
            <SaveButton isSaving={isSaving} disabled={selected.size === 0} onClick={handleSave} />
          </div>
        )}
      </div>
    );
  }

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
            one. Change the selection below if it&apos;s wrong, or add one GraphForge didn&apos;t
            find.
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
          {suggested.map((repo, i) => (
            <RepositoryCheckbox
              key={repo.name}
              repo={repo}
              // "Recommended" for the top of the list the backend already
              // returned in ranked order — not conditioned on the optional
              // `rank` field (real ranked results don't always carry a
              // literal `rank: 1`), just on there being a real reason to
              // point to.
              recommended={i === 0 && Boolean(repo.reason)}
              checked={selected.has(repo.name)}
              onToggle={() => toggle(repo.name)}
              disabled={isSaving}
            />
          ))}
        </div>
      )}

      <div className="pl-6">
        <ManualAddControl
          value={manualName}
          onChange={setManualName}
          onAdd={handleAddManual}
          show={showManualInput}
          onRequestShow={() => setShowManualInput(true)}
        />
      </div>

      {error && <p className="pl-6 text-xs text-danger-fg">{error}</p>}

      {dirty && (
        <div className="pl-6">
          <SaveButton isSaving={isSaving} disabled={selected.size === 0} onClick={handleSave} />
        </div>
      )}
    </div>
  );
}

function SaveButton({
  isSaving,
  disabled,
  onClick,
}: {
  isSaving: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isSaving || disabled}
      className="focus-ring inline-flex items-center gap-1.5 rounded-lg bg-accent-solid px-3 py-1.5 text-xs font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {isSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
      {isSaving ? "Saving…" : "Save selection"}
    </button>
  );
}

/** Names a repository GraphForge's investigation didn't surface at all —
 * the override this saves is exactly the same `repositories` array
 * mechanism the checkboxes above already write to, so nothing about the
 * backend contract changes: a manually-added repository is indistinguishable
 * from one the investigation found once saved. */
function ManualAddControl({
  value,
  onChange,
  onAdd,
  show,
  onRequestShow,
}: {
  value: string;
  onChange: (value: string) => void;
  onAdd: () => void;
  show: boolean;
  onRequestShow?: () => void;
}) {
  if (!show) {
    return (
      <button
        type="button"
        onClick={onRequestShow}
        className="focus-ring inline-flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-fg-secondary"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add a repository GraphForge didn&apos;t find
      </button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onAdd();
          }
        }}
        placeholder="Repository name"
        aria-label="Repository name to add"
        className="focus-ring w-full max-w-[240px] rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-fg placeholder-fg-subtle"
      />
      <button
        type="button"
        onClick={onAdd}
        disabled={!value.trim()}
        className="focus-ring inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Plus className="h-3 w-3" aria-hidden="true" />
        Add
      </button>
    </div>
  );
}

function RepositoryCheckbox({
  repo,
  checked,
  onToggle,
  disabled,
  recommended = false,
}: {
  repo: RepositoryCandidate;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
  recommended?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
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
            {recommended && (
              <span className="rounded bg-accent-bg px-1 py-0.5 text-[10px] font-medium text-accent-fg">
                Recommended
              </span>
            )}
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
              Reason: <span className="text-fg-muted">{displayReason(repo.reason)}</span>
            </span>
          )}
        </span>
      </label>
      {/* Only shown once a repository that started selected has been
          unchecked — an honest statement of consequence, not shown for
          every unchecked suggestion by default (most were never selected
          to begin with, so there's nothing to warn about undoing). */}
      {!checked && repo.selected && (
        <p className="pl-6 text-[10.5px] text-warning-fg">
          This repository won&apos;t be included in the investigation or planning.
        </p>
      )}
    </div>
  );
}
