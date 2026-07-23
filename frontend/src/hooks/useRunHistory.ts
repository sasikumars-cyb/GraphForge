import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { listAgentRuns, type ListRunsParams } from "../lib/api/agentRuns";
import type { RunListItem, RunListResponse } from "../types/agent";

interface UseRunHistoryReturn {
  runs: RunListItem[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  setPage: (page: number) => void;
  refresh: () => void;
}

export function useRunHistory(params: ListRunsParams = {}): UseRunHistoryReturn {
  const { token } = useAuth();
  const [data, setData] = useState<RunListResponse | null>(null);
  const [page, setPage] = useState(params.page ?? 1);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const fetchRuns = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await listAgentRuns(token, { ...params, page });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load run history.");
    } finally {
      setIsLoading(false);
    }
  }, [token, page, params.goal, params.status, params.subject_type, refreshKey]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  return {
    runs: data?.items ?? [],
    total: data?.total ?? 0,
    page,
    pageSize: data?.page_size ?? 25,
    hasMore: data?.has_more ?? false,
    isLoading,
    error,
    setPage,
    refresh: () => setRefreshKey((k) => k + 1),
  };
}
