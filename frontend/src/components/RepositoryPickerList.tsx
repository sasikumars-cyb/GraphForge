import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Search } from "lucide-react";
import { StatusBadge } from "./StatusBadge";
import type { AvailableRepository } from "../types/github";

const ROW_HEIGHT_PX = 44;
/** Below this count, virtualizing buys nothing but complexity — a plain
 * list of a couple dozen rows renders instantly either way. Above it
 * (the org-with-hundreds-of-repos case this exists for), only the rows
 * actually scrolled into view are ever mounted, so the list stays smooth
 * whether it holds 100 repos or 10,000. */
const VIRTUALIZE_THRESHOLD = 30;

/** Tri-state "select all" checkbox — `indeterminate` isn't a settable JSX
 * prop on `<input>`, only a DOM property, so it has to go through a ref. */
function SelectAllCheckbox({
  checked,
  indeterminate,
  onChange,
  label,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: () => void;
  label: string;
}) {
  const ref = useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={onChange}
      aria-label={label}
      className="h-4 w-4 shrink-0 rounded border-line-strong bg-canvas text-info-fg"
    />
  );
}

function RepositoryRow({
  repo,
  checked,
  onToggle,
}: {
  repo: AvailableRepository;
  checked: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <label className="flex h-full cursor-pointer items-center gap-3 border-b border-line-muted px-1 hover:bg-surface-raised">
      <input
        type="checkbox"
        checked={checked}
        onChange={() => onToggle(repo.provider_repo_id)}
        className="h-4 w-4 shrink-0 rounded border-line-strong bg-canvas text-info-fg"
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-fg-secondary">{repo.full_name}</span>
      </span>
      {repo.private && <StatusBadge label="Private" tone="neutral" />}
    </label>
  );
}

/** Search + tri-state select-all + virtualized checklist over a user's
 * available GitHub repositories. Built for the org-with-hundreds-of-repos
 * case: `availableRepos` is fetched once (a live GitHub call, now itself
 * paginated past the first 100 — see `list_repositories` on the backend),
 * filtering is a plain in-memory substring match (trivially fast even at
 * thousands of rows, no debounce needed), and only rows actually
 * scrolled into view are ever mounted via `@tanstack/react-virtual`. */
export function RepositoryPickerList({
  repos,
  selectedIds,
  onChangeSelected,
}: {
  repos: AvailableRepository[];
  selectedIds: Set<string>;
  onChangeSelected: (next: Set<string>) => void;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((repo) => repo.full_name.toLowerCase().includes(q));
  }, [repos, query]);

  const filteredIds = useMemo(() => filtered.map((r) => r.provider_repo_id), [filtered]);
  const selectedInFiltered = useMemo(
    () => filteredIds.filter((id) => selectedIds.has(id)).length,
    [filteredIds, selectedIds],
  );
  const allFilteredSelected = filteredIds.length > 0 && selectedInFiltered === filteredIds.length;
  const someFilteredSelected = selectedInFiltered > 0 && !allFilteredSelected;

  function toggleOne(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    onChangeSelected(next);
  }

  function toggleAllFiltered() {
    const next = new Set(selectedIds);
    if (allFilteredSelected) {
      for (const id of filteredIds) next.delete(id);
    } else {
      for (const id of filteredIds) next.add(id);
    }
    onChangeSelected(next);
  }

  const scrollParentRef = useRef<HTMLDivElement | null>(null);
  const shouldVirtualize = filtered.length > VIRTUALIZE_THRESHOLD;
  const rowVirtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => ROW_HEIGHT_PX,
    overscan: 10,
    enabled: shouldVirtualize,
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="relative w-full max-w-xs">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search repositories…"
            aria-label="Search repositories"
            className="w-full rounded-md border border-line-strong bg-canvas py-1.5 pl-8 pr-3 text-xs text-fg-secondary placeholder-fg-subtle focus:outline-none focus:ring-1 focus:ring-info-fg"
          />
        </div>
        <p className="text-xs text-fg-muted">
          {selectedIds.size} selected · {query ? `${filtered.length} of ${repos.length}` : repos.length}{" "}
          {repos.length === 1 ? "repository" : "repositories"}
        </p>
      </div>

      <div className="flex items-center gap-3 border-b border-line-muted px-1 pb-2">
        <SelectAllCheckbox
          checked={allFilteredSelected}
          indeterminate={someFilteredSelected}
          onChange={toggleAllFiltered}
          label={query ? "Select all matching repositories" : "Select all repositories"}
        />
        <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">
          {query ? "Select all matches" : "Select all"}
        </span>
      </div>

      {filtered.length === 0 ? (
        <p className="py-4 text-center text-sm text-fg-muted">
          No repositories match &ldquo;{query}&rdquo;.
        </p>
      ) : shouldVirtualize ? (
        <div ref={scrollParentRef} className="max-h-72 overflow-y-auto">
          <div
            data-testid="repository-list-total-size"
            style={{ height: rowVirtualizer.getTotalSize(), position: "relative", width: "100%" }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const repo = filtered[virtualRow.index];
              return (
                <div
                  key={repo.provider_repo_id}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  <RepositoryRow
                    repo={repo}
                    checked={selectedIds.has(repo.provider_repo_id)}
                    onToggle={toggleOne}
                  />
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        // Small lists skip the virtualizer entirely — plain scroll, same
        // markup and row component either way.
        <div className="max-h-72 overflow-y-auto">
          {filtered.map((repo) => (
            <div key={repo.provider_repo_id} style={{ height: ROW_HEIGHT_PX }}>
              <RepositoryRow
                repo={repo}
                checked={selectedIds.has(repo.provider_repo_id)}
                onToggle={toggleOne}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
