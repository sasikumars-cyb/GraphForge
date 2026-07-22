import { useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { listTrackedRepositories } from "../lib/api/github";
import { getDeterministicAnalysis } from "../lib/api/analysis";
import { listPullRequests } from "../lib/api/repositories";
import type { RiskLevel as BackendRiskLevel } from "../types/analysis";
import type { RiskLevel } from "../types/domain";

const RISK_TO_DOMAIN: Record<BackendRiskLevel, RiskLevel> = {
  HIGH: "high",
  MEDIUM: "medium",
  LOW: "low",
};

export interface PullRequestRow {
  id: string;
  repositoryId: string;
  repositoryFullName: string;
  number: number;
  title: string;
  authorLogin: string;
  state: string;
  isDraft: boolean;
  risk: RiskLevel | null;
  updatedAt: string;
}

export interface PullRequestsData {
  pullRequests: PullRequestRow[];
  isLoading: boolean;
  error: string | null;
}

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

/** Every tracked pull request across every tracked repository, with its
 * computed risk (null if it hasn't been analyzed yet). */
export function usePullRequestsData(): PullRequestsData {
  const { token } = useAuth();
  const [state, setState] = useState<PullRequestsData>({
    pullRequests: [],
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
        const repos = await listTrackedRepositories(token!);
        const rows: PullRequestRow[] = [];

        for (const repo of repos) {
          const pullRequests = await listPullRequests(token!, repo.id);
          for (const pr of pullRequests) {
            const analysis = await orNull(getDeterministicAnalysis(token!, pr.id));
            rows.push({
              id: pr.id,
              repositoryId: repo.id,
              repositoryFullName: repo.full_name,
              number: pr.number,
              title: pr.title,
              authorLogin: pr.author_login,
              state: pr.state,
              isDraft: pr.is_draft,
              risk: analysis ? RISK_TO_DOMAIN[analysis.risk] : null,
              updatedAt: pr.github_updated_at,
            });
          }
        }

        rows.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

        if (!cancelled) {
          setState({ pullRequests: rows, isLoading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            pullRequests: [],
            isLoading: false,
            error: err instanceof Error ? err.message : "Failed to load pull requests.",
          });
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
