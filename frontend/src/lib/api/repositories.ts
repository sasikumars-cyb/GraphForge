import { apiFetch } from "./client";
import type { NodeTypeCounts } from "../../types/architecture";
import type { CrossRepositoryLink, Graph, GraphEdge, IndexingJob } from "../../types/graph";
import type { PullRequest } from "../../types/pullRequest";
import type { LocalRepositoryCreateRequest, TrackedRepository } from "../../types/github";

export function listPullRequests(token: string, repositoryId: string): Promise<PullRequest[]> {
  return apiFetch<PullRequest[]>(`/repositories/${repositoryId}/pull-requests`, { token });
}

export function triggerIndexing(token: string, repositoryId: string): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { method: "POST", token });
}

export function getLatestIndexingJob(
  token: string,
  repositoryId: string,
  signal?: AbortSignal,
): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { token, signal });
}

export interface GetRepositoryGraphParams {
  /** Max nodes to return — omit to use the backend's own default cap. */
  limit?: number;
  /** Restrict to nodes carrying at least one of these labels (e.g.
   * "Service", "KafkaTopic") — omit to include every type. */
  nodeTypes?: string[];
  /** ADR 0023 — keyset cursor: the previous page's `next_cursor`, to
   * fetch the following page. Omit for the first page. */
  after?: string;
}

export function getRepositoryGraph(
  token: string,
  repositoryId: string,
  params: GetRepositoryGraphParams = {},
  signal?: AbortSignal,
): Promise<Graph> {
  const searchParams = new URLSearchParams();
  if (params.limit !== undefined) searchParams.set("limit", String(params.limit));
  for (const type of params.nodeTypes ?? []) searchParams.append("node_types", type);
  if (params.after !== undefined) searchParams.set("after", params.after);
  const qs = searchParams.toString();
  return apiFetch<Graph>(`/repositories/${repositoryId}/graph${qs ? `?${qs}` : ""}`, {
    token,
    signal,
  });
}

export function getCrossRepositoryLinks(
  token: string,
  repositoryId: string,
  signal?: AbortSignal,
): Promise<CrossRepositoryLink[]> {
  return apiFetch<CrossRepositoryLink[]>(`/repositories/${repositoryId}/cross-repository-links`, {
    token,
    signal,
  });
}

/** All tracked repositories' cross-repository links in one request - used
 * by the Architecture overview instead of one call per repository. */
export function getAllCrossRepositoryLinks(
  token: string,
  signal?: AbortSignal,
): Promise<CrossRepositoryLink[]> {
  return apiFetch<CrossRepositoryLink[]>(`/repositories/cross-repository-links`, {
    token,
    signal,
  });
}

/** Structural repository-to-repository edges (CALLS_SERVICE/SHARES_TOPIC/
 * DEPENDS_ON_REPOSITORY) computed by the cross-repository linker - distinct
 * from `getAllCrossRepositoryLinks` above, which only covers Kafka topic
 * overlap. `source_id`/`target_id` are `"{repository_id}:repository"`. */
export function getAllCrossRepositoryEdges(
  token: string,
  signal?: AbortSignal,
): Promise<GraphEdge[]> {
  return apiFetch<GraphEdge[]>(`/repositories/cross-repository-edges`, { token, signal });
}

/** ADR 0023 — real, untruncated per-label counts for one repository.
 * Backs filter-chip options and their real counts, replacing deriving
 * them client-side from a possibly-`limit`-truncated graph load. */
export function getRepositoryGraphTypes(
  token: string,
  repositoryId: string,
  signal?: AbortSignal,
): Promise<NodeTypeCounts> {
  return apiFetch<NodeTypeCounts>(`/repositories/${repositoryId}/graph/types`, { token, signal });
}

export type NeighborDirection = "any" | "outgoing" | "incoming";

export interface GetNodeNeighborsParams {
  hops?: number;
  edgeTypes?: string[];
  /** "any" (default, matches every existing caller's behavior unchanged)
   * — "outgoing" follows only edges leaving this node (what it depends
   * on), "incoming" only edges pointing at it (what depends on it). The
   * Dependency lens's own direction toggle. */
  direction?: NeighborDirection;
}

/** ADR 0023 — the induced subgraph within `hops` of one node (lazy
 * expand-on-click), not the whole repository's graph. Backs the
 * Architecture page's neighborhood drill-down and the Dependency lens's
 * expand-on-click tree. */
export function getRepositoryGraphNodeNeighbors(
  token: string,
  repositoryId: string,
  nodeId: string,
  params: GetNodeNeighborsParams = {},
  signal?: AbortSignal,
): Promise<Graph> {
  const searchParams = new URLSearchParams();
  if (params.hops !== undefined) searchParams.set("hops", String(params.hops));
  for (const type of params.edgeTypes ?? []) searchParams.append("edge_types", type);
  if (params.direction !== undefined) searchParams.set("direction", params.direction);
  const qs = searchParams.toString();
  return apiFetch<Graph>(
    `/repositories/${repositoryId}/graph/nodes/${encodeURIComponent(nodeId)}/neighbors${qs ? `?${qs}` : ""}`,
    { token, signal },
  );
}

/** ADR 0023 — sets/clears a repository's `domain` (repository grouping).
 * `domain: null` clears it. */
export function updateRepositoryDomain(
  token: string,
  repositoryId: string,
  domain: string | null,
): Promise<TrackedRepository> {
  return apiFetch<TrackedRepository>(`/repositories/${repositoryId}`, {
    method: "PATCH",
    token,
    body: { domain },
  });
}

export function getRepositoryServices(token: string, repositoryId: string): Promise<Graph> {
  return apiFetch<Graph>(`/repositories/${repositoryId}/services`, { token });
}

export function getRepositoryDependencies(token: string, repositoryId: string): Promise<Graph> {
  return apiFetch<Graph>(`/repositories/${repositoryId}/dependencies`, { token });
}

/** Permanently deletes the repository - its pull requests, analyses,
 * indexing jobs, and Neo4j graph are all removed with it. */
export function removeRepository(token: string, repositoryId: string): Promise<void> {
  return apiFetch<void>(`/repositories/${repositoryId}`, { method: "DELETE", token });
}

/** Registers one local-filesystem folder (resolved against the server's
 * configured LOCAL_REPOS_ROOT) as a tracked, indexable repository -
 * additive, unlike the GitHub selection flow which replaces the whole set. */
export function createLocalRepository(
  token: string,
  body: LocalRepositoryCreateRequest,
): Promise<TrackedRepository> {
  return apiFetch<TrackedRepository>("/repositories/local", { method: "POST", token, body });
}
