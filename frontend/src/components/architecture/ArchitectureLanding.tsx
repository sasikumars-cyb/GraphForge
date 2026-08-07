import { useMemo, useState } from "react";
import { AlertTriangle, Folder, Search, X } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import type { ArchitectureRepositorySummary, ArchitectureSummary } from "../../types/architecture";

function StatBlock({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col">
      <span className="font-display text-2xl font-semibold text-fg">{value.toLocaleString()}</span>
      <span className="text-xs text-fg-muted">{label}</span>
    </div>
  );
}

function RepositoryRow({
  repo,
  onSelect,
}: {
  repo: ArchitectureRepositorySummary;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-raised"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-fg-secondary">{repo.full_name}</span>
          <span className="text-xs text-fg-muted">
            {repo.node_count.toLocaleString()} nodes
            {repo.indexing_status === null && " · never indexed"}
            {repo.indexing_status === "failed" && " · last index failed"}
          </span>
        </span>
        {repo.is_stale && <StatusBadge label={repo.indexing_status === null ? "Unindexed" : "Stale"} tone="warning" />}
      </button>
    </li>
  );
}

/** ADR "Architecture Page V2" — the landing experience `GET /architecture/
 * summary` exists to power: org-wide stats, domain-grouped clustering,
 * and a search box over every tracked repository — all from the one
 * request that replaced the old per-repository fan-out. This is the
 * entry point every drill-down (domain -> repository -> graph -> node)
 * starts from. */
export function ArchitectureLanding({
  summary,
  onSelectDomain,
  onSelectRepository,
}: {
  summary: ArchitectureSummary;
  onSelectDomain: (domain: string) => void;
  onSelectRepository: (repo: ArchitectureRepositorySummary) => void;
}) {
  const [query, setQuery] = useState("");

  const matchingRepositories = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null; // null = "no search active", distinct from "search matched nothing"
    return summary.repositories.filter((repo) => repo.full_name.toLowerCase().includes(q));
  }, [summary.repositories, query]);

  const namedDomains = summary.domains.filter(
    (d): d is { domain: string; repository_count: number; node_count: number } => d.domain !== null,
  );
  const ungroupedRepos = summary.repositories.filter((repo) => repo.domain === null);

  return (
    <div className="flex flex-col gap-6">
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted"
          aria-hidden="true"
        />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && query) {
              e.stopPropagation(); // don't let Escape bubble to anything else while there's still text to clear
              setQuery("");
            }
          }}
          placeholder="Find a repository or domain…"
          aria-label="Search repositories and domains"
          className="w-full rounded-lg border border-line-strong bg-canvas py-2.5 pl-10 pr-9 text-sm text-fg placeholder-fg-subtle focus:outline-none focus:ring-1 focus:ring-info-fg"
        />
        {query && (
          <button
            type="button"
            onClick={() => setQuery("")}
            aria-label="Clear search"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-fg-muted hover:text-fg-secondary"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {matchingRepositories !== null ? (
        <Card
          title={`${matchingRepositories.length} matching ${matchingRepositories.length === 1 ? "repository" : "repositories"}`}
        >
          {matchingRepositories.length === 0 ? (
            <p className="py-4 text-center text-sm text-fg-muted">
              No repository or domain matches &ldquo;{query}&rdquo;.
            </p>
          ) : (
            <ul className="divide-y divide-line-muted">
              {matchingRepositories.map((repo) => (
                <RepositoryRow
                  key={repo.repository_id}
                  repo={repo}
                  onSelect={() => onSelectRepository(repo)}
                />
              ))}
            </ul>
          )}
        </Card>
      ) : (
        <>
          <Card>
            <div className="flex flex-wrap items-center gap-8">
              <StatBlock label="Repositories" value={summary.total_repositories} />
              <StatBlock label="Nodes" value={summary.total_nodes} />
              <StatBlock label="Cross-repository edges" value={summary.total_cross_repository_edges} />
              {summary.unindexed_count > 0 && (
                <div className="flex items-center gap-2 text-sm text-warning-fg">
                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                  {summary.unindexed_count} not indexed
                </div>
              )}
              {summary.stale_count > 0 && (
                <div className="flex items-center gap-2 text-sm text-warning-fg">
                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                  {summary.stale_count} stale (30+ days)
                </div>
              )}
            </div>
          </Card>

          {namedDomains.length > 0 && (
            <Card title="Domains" description="Repositories grouped manually — assign one from a repository's own detail view.">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {namedDomains.map((d) => (
                  <button
                    key={d.domain}
                    type="button"
                    onClick={() => onSelectDomain(d.domain)}
                    className="flex flex-col gap-1 rounded-lg border border-line px-4 py-3 text-left hover:border-line-strong hover:bg-surface-raised"
                  >
                    <span className="flex items-center gap-2 text-sm font-medium text-fg-secondary">
                      <Folder className="h-4 w-4 text-fg-muted" aria-hidden="true" />
                      {d.domain}
                    </span>
                    <span className="text-xs text-fg-muted">
                      {d.repository_count.toLocaleString()} repos · {d.node_count.toLocaleString()} nodes
                    </span>
                  </button>
                ))}
              </div>
            </Card>
          )}

          <Card
            title={namedDomains.length > 0 ? "Ungrouped repositories" : "Repositories"}
            description={
              namedDomains.length > 0
                ? "Not assigned to a domain yet."
                : "Select a repository to explore its architecture graph."
            }
          >
            {ungroupedRepos.length === 0 ? (
              <p className="py-4 text-center text-sm text-fg-muted">
                Every tracked repository is assigned to a domain.
              </p>
            ) : (
              <ul className="divide-y divide-line-muted">
                {ungroupedRepos.map((repo) => (
                  <RepositoryRow
                    key={repo.repository_id}
                    repo={repo}
                    onSelect={() => onSelectRepository(repo)}
                  />
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
