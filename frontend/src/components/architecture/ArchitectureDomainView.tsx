import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import type { ArchitectureRepositorySummary } from "../../types/architecture";

/** ADR "Architecture Page V2" — one domain's repositories, drilled into
 * from a landing-page domain card. Reuses the summary already fetched
 * for the landing view (filtered client-side by `domain`) rather than a
 * separate request — the whole org's summary is already in memory. */
export function ArchitectureDomainView({
  domain,
  repositories,
  onSelectRepository,
}: {
  domain: string;
  repositories: ArchitectureRepositorySummary[];
  onSelectRepository: (repo: ArchitectureRepositorySummary) => void;
}) {
  return (
    <Card
      title={domain}
      description={`${repositories.length.toLocaleString()} ${repositories.length === 1 ? "repository" : "repositories"}`}
    >
      <ul className="divide-y divide-line-muted">
        {repositories.map((repo) => (
          <li key={repo.repository_id}>
            <button
              type="button"
              onClick={() => onSelectRepository(repo)}
              className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-raised"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-fg-secondary">{repo.full_name}</span>
                <span className="text-xs text-fg-muted">{repo.node_count.toLocaleString()} nodes</span>
              </span>
              {repo.is_stale && (
                <StatusBadge label={repo.indexing_status === null ? "Unindexed" : "Stale"} tone="warning" />
              )}
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
