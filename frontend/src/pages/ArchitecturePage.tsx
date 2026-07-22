import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../components/Card";
import {
  DependencyGraph,
  NODE_LABEL_COLORS,
  RepositoryOverviewGraph,
  type RepositorySummary,
} from "../components/graph/DependencyGraph";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import {
  getAllCrossRepositoryLinks,
  getCrossRepositoryLinks,
  getLatestIndexingJob,
  getRepositoryGraph,
} from "../lib/api/repositories";
import { buildRepositoryDependencyEdges, mergeCrossRepositoryLinks } from "../lib/graph/mergeGraphs";
import type { TrackedRepository } from "../types/github";
import type { CrossRepositoryLink, Graph } from "../types/graph";

const EDGE_LEGEND: { type: string; description: string }[] = [
  { type: "CONTAINS", description: "Repository contains a component/dependency" },
  { type: "EXPOSES", description: "Controller exposes an endpoint" },
  { type: "CALLS", description: "Feign client calls a remote endpoint" },
  { type: "PRODUCES_TO", description: "Publishes to a Kafka topic" },
  { type: "CONSUMES_FROM", description: "Consumes from a Kafka topic" },
  { type: "DEPENDS_ON", description: "Depends on a Maven artifact" },
];

export function ArchitecturePage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  // Full node/edge graphs are only ever fetched lazily, per repository, the
  // first time that repository is expanded - never eagerly for the whole
  // org, so opening this page stays cheap regardless of how many
  // repositories are tracked.
  const [graphsByRepoId, setGraphsByRepoId] = useState<Record<string, Graph>>({});
  const [summariesByRepoId, setSummariesByRepoId] = useState<
    Record<string, Record<string, number> | null>
  >({});
  const [selectedRepoId, setSelectedRepoId] = useState<string>(
    searchParams.get("repository") ?? "all",
  );
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingSelectedGraph, setIsLoadingSelectedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cached once for the overview - never refetched on hover.
  const [allLinks, setAllLinks] = useState<CrossRepositoryLink[] | null>(null);

  // Cheap initial load: repository list + each repository's lightweight
  // indexing summary counts (not its full graph) - enough to render one
  // overview card per repository.
  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;

    async function load() {
      try {
        const repos = await listTrackedRepositories(token!);
        const summaries = await Promise.all(
          repos.map((repo) =>
            getLatestIndexingJob(token!, repo.id)
              .then((job) => job.result_summary)
              .catch(() => null),
          ),
        );
        if (!cancelled) {
          setRepositories(repos);
          setSummariesByRepoId(Object.fromEntries(repos.map((repo, i) => [repo.id, summaries[i]])));
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load repositories.");
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Overview repository-to-repository edges: one request for every tracked
  // repository's cross-repository links at once (one Neo4j relationship
  // query server-side), fetched once and cached - never per hover, and no
  // longer one HTTP request per repository.
  useEffect(() => {
    if (!token || repositories.length === 0 || allLinks !== null) {
      return;
    }
    let cancelled = false;

    async function loadLinks() {
      const links = await getAllCrossRepositoryLinks(token!).catch(() => []);
      if (!cancelled) {
        setAllLinks(links);
      }
    }

    void loadLinks();
    return () => {
      cancelled = true;
    };
  }, [token, repositories, allLinks]);

  // Lazy load: only fetch a repository's own graph once it's expanded, plus
  // one lightweight `/cross-repository-links` call to discover which other
  // repositories it's connected to - never any other repository's full
  // graph. Exactly two requests per expand.
  useEffect(() => {
    if (!token || selectedRepoId === "all" || graphsByRepoId[selectedRepoId]) {
      return;
    }
    let cancelled = false;

    async function loadSelected() {
      setIsLoadingSelectedGraph(true);
      const [ownGraph, links] = await Promise.all([
        getRepositoryGraph(token!, selectedRepoId),
        getCrossRepositoryLinks(token!, selectedRepoId).catch(() => []),
      ]);
      if (cancelled) return;
      setGraphsByRepoId((prev) => ({
        ...prev,
        [selectedRepoId]: mergeCrossRepositoryLinks(ownGraph, links),
      }));
      setIsLoadingSelectedGraph(false);
    }

    void loadSelected();
    return () => {
      cancelled = true;
    };
  }, [token, selectedRepoId, graphsByRepoId]);

  const graph: Graph | null = selectedRepoId === "all" ? null : (graphsByRepoId[selectedRepoId] ?? null);
  const repositoryNameById = Object.fromEntries(repositories.map((r) => [r.id, r.full_name]));
  const repositorySummaries: RepositorySummary[] = repositories.map((r) => {
    const summary = summariesByRepoId[r.id];
    return {
      id: r.id,
      name: r.full_name,
      components:
        summary && (summary.controllers ?? summary.services ?? summary.feign_clients) !== undefined
          ? (summary.controllers ?? 0) + (summary.services ?? 0) + (summary.feign_clients ?? 0)
          : undefined,
      externalDependencies: summary?.maven_dependencies,
      messagingTouchpoints:
        summary && (summary.kafka_producers ?? summary.kafka_consumers) !== undefined
          ? (summary.kafka_producers ?? 0) + (summary.kafka_consumers ?? 0)
          : undefined,
    };
  });
  const repositoryDependencyEdges = allLinks ? buildRepositoryDependencyEdges(allLinks) : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-50">Architecture</h2>
          <p className="mt-1 text-sm text-slate-400">
            The dependency graph generated from indexed repositories, showing relationships
            between repositories, modules, services, APIs, data stores, messaging systems, and
            other software components.
          </p>
        </div>
        {repositories.length > 0 && (
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Repository
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100"
            >
              <option value="all">All repositories (merged)</option>
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card
        title="Dependency graph"
        description={
          selectedRepoId === "all"
            ? "One summary card per repository - select or expand a repository to load its detailed dependency graph on demand."
            : "This repository's own dependencies, plus any other repositories it shares a component with (inbound and outbound)."
        }
        action={
          selectedRepoId !== "all" && (
            <button
              type="button"
              onClick={() => setSelectedRepoId("all")}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500"
            >
              ← Back to overview
            </button>
          )
        }
      >
        {isLoading ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-slate-500">
            Loading repositories…
          </div>
        ) : repositories.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
            <p className="text-sm">No repositories tracked yet.</p>
          </div>
        ) : selectedRepoId === "all" ? (
          <RepositoryOverviewGraph
            repositories={repositorySummaries}
            edges={repositoryDependencyEdges}
            onExpand={(repoId) => setSelectedRepoId(repoId)}
          />
        ) : isLoadingSelectedGraph || !graph ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-slate-500">
            Loading this repository's graph…
          </div>
        ) : graph.nodes.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
            <p className="text-sm">No graph data yet - index this repository first.</p>
          </div>
        ) : (
          <DependencyGraph graph={graph} repositoryNameById={repositoryNameById} />
        )}
      </Card>

      <Card title="Legend" description="Node and edge types">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Nodes</p>
            <ul className="flex flex-col gap-1.5 text-sm text-slate-300">
              {Object.entries(NODE_LABEL_COLORS).map(([label, colors]) => (
                <li key={label} className="flex items-center gap-2">
                  <span
                    className="h-3 w-3 rounded-sm"
                    style={{ background: colors.background, border: `1px solid ${colors.border}` }}
                  />
                  {label}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Edges</p>
            <ul className="flex flex-col gap-1.5 text-sm text-slate-300">
              {EDGE_LEGEND.map(({ type, description }) => (
                <li key={type}>
                  <span className="font-mono text-xs text-slate-400">{type}</span> — {description}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Card>
    </div>
  );
}
