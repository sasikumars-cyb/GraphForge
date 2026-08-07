import { useState } from "react";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { ArchitectureBreadcrumbs } from "../components/architecture/ArchitectureBreadcrumbs";
import { ArchitectureDomainView } from "../components/architecture/ArchitectureDomainView";
import { ArchitectureLanding } from "../components/architecture/ArchitectureLanding";
import { RepositoryGraphExplorer } from "../components/architecture/RepositoryGraphExplorer";
import type { ArchitectureView } from "../components/architecture/types";
import { EmptyState, SampleGraph } from "../components/EmptyState";
import { useAuth } from "../app/auth-context";
import { getArchitectureSummary } from "../lib/api/architecture";
import { updateRepositoryDomain } from "../lib/api/repositories";
import { primaryLabel } from "../components/graph/graphLabels";
import type { ArchitectureRepositorySummary } from "../types/architecture";
import type { GraphNode } from "../types/graph";

/**
 * Architecture Page V2 — progressive exploration, not a single "load
 * everything" view. `GET /architecture/summary` (ADR 0023) is the
 * landing experience: org-wide stats and domain-grouped repository
 * cards, one request, no per-repository fan-out. Drilling into a domain,
 * then a repository, then a node's neighborhood each load only what that
 * level needs — a repository's own graph is fetched lazily (paginated,
 * `RepositoryGraphExplorer`) only once it's actually selected, and a
 * node's neighborhood only once "Explore neighbors" is clicked. This is
 * what keeps the page responsive at hundreds of repositories and
 * millions of nodes: nothing beyond the current view level is ever
 * fetched or rendered.
 */
export function ArchitecturePage() {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [view, setView] = useState<ArchitectureView>({ level: "landing" });

  const summaryQuery = useQuery({
    queryKey: ["architecture-summary"],
    queryFn: ({ signal }) => getArchitectureSummary(token as string, signal),
    enabled: token !== null,
  });

  async function saveDomain(repositoryId: string, domain: string | null) {
    if (!token) return;
    await updateRepositoryDomain(token, repositoryId, domain);
    // The summary drives both the landing page's domain grouping and the
    // breadcrumb trail — refetch rather than patch the cache by hand so
    // `domains[]`'s aggregate counts (which this single-repository update
    // can shift) stay correct too.
    await queryClient.invalidateQueries({ queryKey: ["architecture-summary"] });
    // Keep the current view's own `domain` in sync so the breadcrumb and
    // this same editor reflect the save immediately, without waiting on
    // the (already in-flight) summary refetch above.
    setView((current) =>
      current.level === "repository" || current.level === "neighborhood"
        ? { ...current, domain }
        : current,
    );
  }

  function selectRepository(repo: ArchitectureRepositorySummary) {
    setView({
      level: "repository",
      repositoryId: repo.repository_id,
      repositoryName: repo.full_name,
      domain: repo.domain,
    });
  }

  function exploreNeighbors(node: GraphNode) {
    if (view.level !== "repository") return;
    setView({
      level: "neighborhood",
      repositoryId: view.repositoryId,
      repositoryName: view.repositoryName,
      domain: view.domain,
      nodeId: node.id,
      nodeLabel: String(node.properties.name ?? primaryLabel(node.labels)),
    });
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-fg">Architecture</h1>
        <p className="mt-1 text-sm text-fg-muted">
          The knowledge graph generated from indexed repositories — services, APIs, data stores,
          messaging systems, and the relationships between them.
        </p>
      </div>

      {view.level !== "landing" && (
        <ArchitectureBreadcrumbs view={view} onNavigate={setView} />
      )}

      {summaryQuery.isPending ? (
        // Matches MetricsPage's own skeleton convention — a stat-bar shape
        // plus a couple of card-shaped blocks, not the plain "Loading…"
        // text this page shipped with initially.
        <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading architecture summary">
          <div className="h-20 animate-pulse rounded-xl bg-surface" />
          <div className="h-40 animate-pulse rounded-xl bg-surface" />
          <div className="h-40 animate-pulse rounded-xl bg-surface" />
        </div>
      ) : summaryQuery.isError ? (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          Failed to load the architecture summary.
        </div>
      ) : summaryQuery.data.total_repositories === 0 ? (
        <EmptyState
          illustration={<SampleGraph />}
          title="Your architecture graph appears here"
          description="GraphForge indexes your repositories and maps every service, API, topic, and table — plus how they depend on each other. Connect GitHub and select a repository to build the first one."
          actions={[
            { label: "Connect GitHub", to: "/settings" },
            { label: "Manage repositories", to: "/repositories" },
          ]}
        />
      ) : view.level === "landing" ? (
        <ArchitectureLanding
          summary={summaryQuery.data}
          onSelectDomain={(domain) => setView({ level: "domain", domain })}
          onSelectRepository={selectRepository}
        />
      ) : view.level === "domain" ? (
        <ArchitectureDomainView
          domain={view.domain}
          repositories={summaryQuery.data.repositories.filter((r) => r.domain === view.domain)}
          onSelectRepository={selectRepository}
        />
      ) : view.level === "repository" ? (
        <RepositoryGraphExplorer
          key={view.repositoryId}
          repositoryId={view.repositoryId}
          repositoryName={view.repositoryName}
          mode={{ kind: "full" }}
          onExploreNeighbors={exploreNeighbors}
          domain={view.domain}
          onDomainChange={(domain) => saveDomain(view.repositoryId, domain)}
        />
      ) : (
        <RepositoryGraphExplorer
          key={`${view.repositoryId}:${view.nodeId}`}
          repositoryId={view.repositoryId}
          repositoryName={view.repositoryName}
          mode={{ kind: "neighborhood", nodeId: view.nodeId }}
          onExploreNeighbors={exploreNeighbors}
        />
      )}
    </div>
  );
}
