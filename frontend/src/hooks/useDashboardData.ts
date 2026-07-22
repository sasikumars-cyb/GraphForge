import { useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { listTrackedRepositories } from "../lib/api/github";
import { getDeterministicAnalysis } from "../lib/api/analysis";
import { getLatestIndexingJob, listPullRequests } from "../lib/api/repositories";
import type { TrackedRepository } from "../types/github";
import type { PullRequest } from "../types/pullRequest";
import type { RiskLevel as BackendRiskLevel } from "../types/analysis";
import type { RepositoryHealth, RiskLevel } from "../types/domain";

const RISK_TO_DOMAIN: Record<BackendRiskLevel, RiskLevel> = {
  HIGH: "high",
  MEDIUM: "medium",
  LOW: "low",
};

export interface DashboardPullRequestRow {
  id: string;
  repositoryId: string;
  title: string;
  repositoryFullName: string;
  state: string;
  isDraft: boolean;
  risk: RiskLevel | null;
  updatedAt: string;
}

export interface DashboardRepositoryRow {
  id: string;
  name: string;
  fullName: string;
  createdAt: string;
  health: RepositoryHealth;
  openPullRequests: number;
}

export interface DashboardStats {
  repositoriesMonitored: number;
  organizationCount: number;
  openPullRequestCount: number;
  awaitingAnalysisCount: number;
  highRiskThisWeekCount: number;
  avgIndexingTimeLabel: string;
}

export interface DashboardData {
  stats: DashboardStats;
  recentPullRequests: DashboardPullRequestRow[];
  repositories: DashboardRepositoryRow[];
  isLoading: boolean;
  error: string | null;
}

const EMPTY_STATS: DashboardStats = {
  repositoriesMonitored: 0,
  organizationCount: 0,
  openPullRequestCount: 0,
  awaitingAnalysisCount: 0,
  highRiskThisWeekCount: 0,
  avgIndexingTimeLabel: "—",
};

function formatDuration(ms: number): string {
  if (ms < 1000) {
    return "<1s";
  }
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

/** 404 from a not-yet-run analysis or indexing job is expected, not an error. */
async function orNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

export function useDashboardData(): DashboardData {
  const { token } = useAuth();
  const [state, setState] = useState<DashboardData>({
    stats: EMPTY_STATS,
    recentPullRequests: [],
    repositories: [],
    isLoading: true,
    error: null,
  });

  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;

    async function load() {
      try {
        const repos: TrackedRepository[] = await listTrackedRepositories(token!);

        const perRepo = await Promise.all(
          repos.map(async (repo) => {
            const [pullRequests, indexingJob] = await Promise.all([
              listPullRequests(token!, repo.id),
              orNull(getLatestIndexingJob(token!, repo.id)),
            ]);
            return { repo, pullRequests, indexingJob };
          }),
        );

        const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
        const rows: DashboardPullRequestRow[] = [];
        const repoRows: DashboardRepositoryRow[] = [];
        let openPullRequestCount = 0;
        let awaitingAnalysisCount = 0;
        let highRiskThisWeekCount = 0;
        const indexingDurationsMs: number[] = [];

        for (const { repo, pullRequests, indexingJob } of perRepo) {
          if (indexingJob?.started_at && indexingJob?.finished_at) {
            indexingDurationsMs.push(
              new Date(indexingJob.finished_at).getTime() -
                new Date(indexingJob.started_at).getTime(),
            );
          }

          const openPrs = pullRequests.filter((pr: PullRequest) => pr.state === "open");
          openPullRequestCount += openPrs.length;

          let repoHasHighRisk = false;
          let repoHasUnresolved = false;

          for (const pr of openPrs) {
            const analysis = await orNull(getDeterministicAnalysis(token!, pr.id));
            const risk = analysis ? RISK_TO_DOMAIN[analysis.risk] : null;

            if (!analysis) {
              awaitingAnalysisCount += 1;
              repoHasUnresolved = true;
            } else if (risk === "high") {
              repoHasHighRisk = true;
              if (new Date(pr.github_updated_at).getTime() >= oneWeekAgo) {
                highRiskThisWeekCount += 1;
              }
            } else if (risk === "medium") {
              repoHasUnresolved = true;
            }

            rows.push({
              id: pr.id,
              repositoryId: repo.id,
              title: pr.title,
              repositoryFullName: repo.full_name,
              state: pr.state,
              isDraft: pr.is_draft,
              risk,
              updatedAt: pr.github_updated_at,
            });
          }

          repoRows.push({
            id: repo.id,
            name: repo.name,
            fullName: repo.full_name,
            createdAt: repo.created_at,
            health: repoHasHighRisk ? "critical" : repoHasUnresolved ? "attention" : "healthy",
            openPullRequests: openPrs.length,
          });
        }

        rows.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

        const avgIndexingTimeLabel =
          indexingDurationsMs.length > 0
            ? formatDuration(
                indexingDurationsMs.reduce((sum, ms) => sum + ms, 0) / indexingDurationsMs.length,
              )
            : "—";

        if (!cancelled) {
          setState({
            stats: {
              repositoriesMonitored: repos.length,
              organizationCount: new Set(repos.map((r) => r.owner)).size,
              openPullRequestCount,
              awaitingAnalysisCount,
              highRiskThisWeekCount,
              avgIndexingTimeLabel,
            },
            recentPullRequests: rows,
            repositories: repoRows,
            isLoading: false,
            error: null,
          });
        }
      } catch (err) {
        if (!cancelled) {
          setState((prev) => ({
            ...prev,
            isLoading: false,
            error: err instanceof Error ? err.message : "Failed to load dashboard data.",
          }));
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return state;
}
