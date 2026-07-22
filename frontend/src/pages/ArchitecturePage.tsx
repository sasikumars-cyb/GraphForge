import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../components/Card";
import { DependencyGraph, NODE_LABEL_COLORS } from "../components/graph/DependencyGraph";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import { getRepositoryGraph } from "../lib/api/repositories";
import { mergeGraphs } from "../lib/graph/mergeGraphs";
import type { TrackedRepository } from "../types/github";
import type { Graph } from "../types/graph";

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
  const [graphsByRepoId, setGraphsByRepoId] = useState<Record<string, Graph>>({});
  const [selectedRepoId, setSelectedRepoId] = useState<string>(
    searchParams.get("repository") ?? "all",
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;

    async function load() {
      try {
        const repos = await listTrackedRepositories(token!);
        const graphs = await Promise.all(repos.map((repo) => getRepositoryGraph(token!, repo.id)));
        if (!cancelled) {
          setRepositories(repos);
          setGraphsByRepoId(Object.fromEntries(repos.map((repo, i) => [repo.id, graphs[i]])));
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load the architecture graph.");
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const graph: Graph =
    selectedRepoId === "all"
      ? mergeGraphs(Object.values(graphsByRepoId))
      : (graphsByRepoId[selectedRepoId] ?? { nodes: [], edges: [] });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-50">Architecture</h2>
          <p className="mt-1 text-sm text-slate-400">
            The dependency graph ChangeGuard builds from your indexed repositories, including
            cross-repository Kafka topic coupling.
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
            ? "All tracked repositories merged - shared Kafka topics are deduplicated by name so cross-repo coupling is visible as a single node."
            : "This repository's own graph."
        }
      >
        {isLoading ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-slate-500">
            Loading graph…
          </div>
        ) : graph.nodes.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
            <p className="text-sm">
              {repositories.length === 0
                ? "No repositories tracked yet."
                : "No graph data yet - index a repository first."}
            </p>
          </div>
        ) : (
          <DependencyGraph graph={graph} />
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
