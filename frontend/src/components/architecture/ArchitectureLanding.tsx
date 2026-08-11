import { useMemo, useState } from "react";
import { AlertTriangle, Boxes, Search, X } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { ProvenanceTag } from "../intelligence/ProvenanceTag";
import { Treemap, type TreemapItem } from "../Treemap";
import { formatRelativeTime } from "../../lib/formatDate";
import type { ArchitectureRepositorySummary, ArchitectureSummary } from "../../types/architecture";

type SizeMetric = "node_count" | "repository_count";

const UNGROUPED_COLOR = {
  background: "var(--gf-surface-raised)",
  text: "var(--gf-fg-muted)",
  border: "var(--gf-line-strong)",
};

function StatBlock({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col">
      <span className="font-display text-2xl font-semibold tabular-nums text-fg">
        {value.toLocaleString()}
      </span>
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
        className="focus-ring flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left hover:bg-surface-raised"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-fg-secondary">{repo.full_name}</span>
          <span className="text-xs text-fg-muted">
            {repo.node_count.toLocaleString()} nodes
            {repo.indexing_status === null && " · never indexed"}
            {repo.indexing_status === "failed" && " · last index failed"}
          </span>
        </span>
        {repo.is_stale && (
          <StatusBadge label={repo.indexing_status === null ? "Unindexed" : "Stale"} tone="warning" />
        )}
      </button>
    </li>
  );
}

/** What a repository row in "Needs attention" is actually flagging —
 * ordered by urgency (a failed index is more actionable right now than
 * "hasn't been reindexed in a month"). Purely a read of
 * `indexing_status`/`is_stale`, nothing inferred. */
function attentionReason(repo: ArchitectureRepositorySummary): string {
  if (repo.indexing_status === "failed") return "Last indexing attempt failed";
  if (repo.indexing_status === null) return "Never indexed — no architecture graph exists yet";
  if (repo.last_indexed_at) return `Stale — last indexed ${formatRelativeTime(repo.last_indexed_at)}`;
  return "Stale";
}

const ATTENTION_PRIORITY: Record<string, number> = { failed: 0, unindexed: 1, stale: 2 };

function attentionRank(repo: ArchitectureRepositorySummary): number {
  if (repo.indexing_status === "failed") return ATTENTION_PRIORITY.failed;
  if (repo.indexing_status === null) return ATTENTION_PRIORITY.unindexed;
  return ATTENTION_PRIORITY.stale;
}

/** ADR "Architecture Page V2" — the landing experience `GET /architecture/
 * summary` exists to power: org-wide stats, domain-grouped clustering,
 * and a search box over every tracked repository — all from the one
 * request that replaced the old per-repository fan-out. This is the
 * entry point every drill-down (domain -> repository -> graph -> node)
 * starts from.
 *
 * Redesigned from "stats card + flat repository list" into something that
 * answers, before any click: what does GraphForge know about this
 * landscape, what needs attention, and what's actually large enough to
 * matter. Every number below is read or computed directly from `summary`
 * — nothing here is an LLM claim, which is why "Largest repositories" is
 * labelled "by graph size" rather than anything that sounds like a
 * judgment GraphForge didn't actually make. See `ProvenanceTag`. */
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
  const [sizeMetric, setSizeMetric] = useState<SizeMetric>("node_count");

  const matchingRepositories = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null; // null = "no search active", distinct from "search matched nothing"
    return summary.repositories.filter((repo) => repo.full_name.toLowerCase().includes(q));
  }, [summary.repositories, query]);

  const namedDomains = summary.domains.filter(
    (d): d is { domain: string; repository_count: number; node_count: number } => d.domain !== null,
  );
  const ungroupedDomain = summary.domains.find((d) => d.domain === null);
  const ungroupedRepos = summary.repositories.filter((repo) => repo.domain === null);

  const attentionRepos = useMemo(
    () =>
      summary.repositories
        .filter((r) => r.is_stale || r.indexing_status === null || r.indexing_status === "failed")
        .sort((a, b) => attentionRank(a) - attentionRank(b))
        .slice(0, 6),
    [summary.repositories],
  );

  // "What's large enough to matter" — the one honest proxy for
  // significance this endpoint's data actually supports. Deliberately
  // *not* called "most important" or "highest risk": node count is graph
  // size, not business criticality, and the copy says so.
  const largestRepos = useMemo(
    () =>
      [...summary.repositories]
        .filter((r) => r.node_count > 0)
        .sort((a, b) => b.node_count - a.node_count)
        .slice(0, 5),
    [summary.repositories],
  );

  // The Ownership lens's own signature visualization
  // (ARCHITECTURE_EXPERIENCE_REDESIGN.md): area proportional to size, so
  // "which domain owns the most" is a shape to glance at, not a column of
  // numbers to compare. The Ungrouped bucket rides along in the same
  // treemap (a fixed neutral grey, not the categorical palette real
  // domains use) specifically so an outsized "Unowned" block is as
  // visible as the doc's own read of it — but stays inert (its repos are
  // already one scroll away, in the list below) rather than a drill-in
  // target like a real domain.
  const domainTreemapItems: TreemapItem[] = useMemo(() => {
    const items: TreemapItem[] = namedDomains.map((d) => ({
      id: d.domain,
      label: d.domain,
      value: d[sizeMetric],
      sublabel: `${d.repository_count.toLocaleString()} repos · ${d.node_count.toLocaleString()} nodes`,
    }));
    if (ungroupedDomain && ungroupedDomain.repository_count > 0) {
      items.push({
        id: "__ungrouped__",
        label: "Ungrouped",
        value: ungroupedDomain[sizeMetric],
        sublabel: `${ungroupedDomain.repository_count.toLocaleString()} repos · ${ungroupedDomain.node_count.toLocaleString()} nodes`,
        color: UNGROUPED_COLOR,
        disabled: true,
      });
    }
    return items;
  }, [namedDomains, ungroupedDomain, sizeMetric]);

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
          {/* ── Coverage — what GraphForge knows, at a glance ────── */}
          <Card>
            <div className="flex flex-wrap items-center gap-8">
              <StatBlock label="Repositories" value={summary.total_repositories} />
              <StatBlock label="Nodes" value={summary.total_nodes} />
              <StatBlock label="Cross-repository edges" value={summary.total_cross_repository_edges} />
              <div className="ml-auto">
                <ProvenanceTag kind="fact" label="Indexed from GitHub" />
              </div>
            </div>
          </Card>

          {/* ── Needs attention — knowledge gaps, not just a badge
              buried in a list further down. Same vocabulary as Mission
              Control's own "Needs your attention": a coloured count
              chip, oldest/most-actionable first, each row a direct
              jump to fix it. ─────────────────────────────────────── */}
          {attentionRepos.length > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
                <span
                  aria-hidden="true"
                  className="flex h-5 w-5 items-center justify-center rounded-full bg-warning-solid text-[11px] font-bold text-warning-on-solid"
                >
                  {summary.unindexed_count + summary.stale_count}
                </span>
                Needs attention
              </h2>
              <div className="divide-y divide-line-muted rounded-xl border border-warning-line/30 bg-warning-bg/40">
                {attentionRepos.map((repo) => (
                  <button
                    key={repo.repository_id}
                    type="button"
                    onClick={() => onSelectRepository(repo)}
                    className="focus-ring flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-hover"
                  >
                    <AlertTriangle
                      className={`h-4 w-4 shrink-0 ${
                        repo.indexing_status === "failed" ? "text-danger-fg" : "text-warning-fg"
                      }`}
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-fg">{repo.full_name}</p>
                      <p className="text-xs text-fg-muted">{attentionReason(repo)}</p>
                    </div>
                    <span className="shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold text-fg-secondary ring-1 ring-inset ring-line">
                      Explore
                    </span>
                  </button>
                ))}
              </div>
              {summary.unindexed_count + summary.stale_count > attentionRepos.length && (
                <p className="px-1 text-xs text-fg-subtle">
                  +{summary.unindexed_count + summary.stale_count - attentionRepos.length} more —
                  search by name above to find them.
                </p>
              )}
            </section>
          )}

          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[1fr_minmax(280px,340px)]">
            {namedDomains.length > 0 && (
              <Card
                title="Ownership landscape"
                description="Repositories grouped by domain — assign one from a repository's own detail view."
                action={
                  <div
                    role="group"
                    aria-label="Treemap sizing metric"
                    className="flex items-center gap-0.5 rounded-full border border-line p-0.5 text-xs"
                  >
                    {(["node_count", "repository_count"] as const).map((metric) => (
                      <button
                        key={metric}
                        type="button"
                        aria-pressed={sizeMetric === metric}
                        onClick={() => setSizeMetric(metric)}
                        className={`rounded-full px-2.5 py-1 transition-colors ${
                          sizeMetric === metric
                            ? "bg-info-bg text-info-fg"
                            : "text-fg-secondary hover:bg-surface-raised"
                        }`}
                      >
                        {metric === "node_count" ? "By nodes" : "By repos"}
                      </button>
                    ))}
                  </div>
                }
              >
                <Treemap
                  items={domainTreemapItems}
                  onSelect={(item) => onSelectDomain(item.id)}
                  ariaLabel="Domains by size"
                />
              </Card>
            )}

            {/* ── Largest repositories — "what's big enough to matter",
                the one honest significance signal this data supports.
                Framed explicitly as graph size, not business
                importance GraphForge has no basis to claim. ────────── */}
            {largestRepos.length > 0 && (
              <Card
                title="Largest repositories"
                description="By graph size — the biggest surface to understand or affect."
                action={<ProvenanceTag kind="derived" />}
              >
                <ol className="flex flex-col gap-1">
                  {largestRepos.map((repo, i) => (
                    <li key={repo.repository_id}>
                      <button
                        type="button"
                        onClick={() => onSelectRepository(repo)}
                        className="focus-ring flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface-raised"
                      >
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-raised text-[11px] font-bold text-fg-muted">
                          {i + 1}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm text-fg-secondary">
                          {repo.full_name}
                        </span>
                        <span className="shrink-0 font-mono text-xs text-fg-muted">
                          {repo.node_count.toLocaleString()}
                        </span>
                      </button>
                    </li>
                  ))}
                </ol>
              </Card>
            )}
          </div>

          <Card
            title={namedDomains.length > 0 ? "Ungrouped repositories" : "Repositories"}
            description={
              namedDomains.length > 0
                ? "Not assigned to a domain yet."
                : "Select a repository to explore its architecture graph."
            }
            action={
              <span className="flex items-center gap-1.5 text-xs text-fg-subtle">
                <Boxes className="h-3.5 w-3.5" aria-hidden="true" />
                Click any repository to open its dependency graph
              </span>
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
