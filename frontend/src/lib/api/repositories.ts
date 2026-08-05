import { apiFetch } from "./client";
import type { CrossRepositoryLink, Graph, GraphEdge, IndexingJob } from "../../types/graph";
import type { PullRequest } from "../../types/pullRequest";
import type { LocalRepositoryCreateRequest, TrackedRepository } from "../../types/github";

export function listPullRequests(token: string, repositoryId: string): Promise<PullRequest[]> {
  return apiFetch<PullRequest[]>(`/repositories/${repositoryId}/pull-requests`, { token });
}

export function triggerIndexing(token: string, repositoryId: string): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { method: "POST", token });
}

export function getLatestIndexingJob(token: string, repositoryId: string): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { token });
}

export interface GetRepositoryGraphParams {
  /** Max nodes to return — omit to use the backend's own default cap. */
  limit?: number;
  /** Restrict to nodes carrying at least one of these labels (e.g.
   * "Service", "KafkaTopic") — omit to include every type. */
  nodeTypes?: string[];
}

export function getRepositoryGraph(
  token: string,
  repositoryId: string,
  params: GetRepositoryGraphParams = {},
): Promise<Graph> {
  const searchParams = new URLSearchParams();
  if (params.limit !== undefined) searchParams.set("limit", String(params.limit));
  for (const type of params.nodeTypes ?? []) searchParams.append("node_types", type);
  const qs = searchParams.toString();
  return apiFetch<Graph>(`/repositories/${repositoryId}/graph${qs ? `?${qs}` : ""}`, { token });
}

export function getCrossRepositoryLinks(
  token: string,
  repositoryId: string,
): Promise<CrossRepositoryLink[]> {
  return apiFetch<CrossRepositoryLink[]>(`/repositories/${repositoryId}/cross-repository-links`, {
    token,
  });
}

/** All tracked repositories' cross-repository links in one request - used
 * by the Architecture overview instead of one call per repository. */
export function getAllCrossRepositoryLinks(token: string): Promise<CrossRepositoryLink[]> {
  return apiFetch<CrossRepositoryLink[]>(`/repositories/cross-repository-links`, { token });
}

/** Structural repository-to-repository edges (CALLS_SERVICE/SHARES_TOPIC/
 * DEPENDS_ON_REPOSITORY) computed by the cross-repository linker - distinct
 * from `getAllCrossRepositoryLinks` above, which only covers Kafka topic
 * overlap. `source_id`/`target_id` are `"{repository_id}:repository"`. */
export function getAllCrossRepositoryEdges(token: string): Promise<GraphEdge[]> {
  return apiFetch<GraphEdge[]>(`/repositories/cross-repository-edges`, { token });
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
