import { apiFetch } from "./client";
import type { CrossRepositoryLink, Graph, IndexingJob } from "../../types/graph";
import type { PullRequest } from "../../types/pullRequest";

export function listPullRequests(token: string, repositoryId: string): Promise<PullRequest[]> {
  return apiFetch<PullRequest[]>(`/repositories/${repositoryId}/pull-requests`, { token });
}

export function triggerIndexing(token: string, repositoryId: string): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { method: "POST", token });
}

export function getLatestIndexingJob(token: string, repositoryId: string): Promise<IndexingJob> {
  return apiFetch<IndexingJob>(`/repositories/${repositoryId}/index`, { token });
}

export function getRepositoryGraph(token: string, repositoryId: string): Promise<Graph> {
  return apiFetch<Graph>(`/repositories/${repositoryId}/graph`, { token });
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

export function getRepositoryServices(token: string, repositoryId: string): Promise<Graph> {
  return apiFetch<Graph>(`/repositories/${repositoryId}/services`, { token });
}

export function getRepositoryDependencies(token: string, repositoryId: string): Promise<Graph> {
  return apiFetch<Graph>(`/repositories/${repositoryId}/dependencies`, { token });
}
