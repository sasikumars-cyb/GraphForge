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

  // Stabilize filter values to avoid recreating the callback on every render
  const goal = params.goal;
  const filterStatus = params.status;
  const subjectType = params.subject_type;

  const fetchRuns = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      setIsLoading(true);
      setError(null);
      try {
        const result = await listAgentRuns(
          token,
          { goal, status: filterStatus, subject_type: subjectType, page },
          signal,
        );
        if (signal?.aborted) return;
        setData(result);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load run history.");
      } finally {
        if (!signal?.aborted) setIsLoading(false);
      }
    },
    [token, page, goal, filterStatus, subjectType, refreshKey],
  );

  useEffect(() => {
    // Aborts a still-in-flight fetch for the previous page/filters if
    // `page`/`goal`/`filterStatus`/`subjectType` changes again quickly
    // (e.g. rapid Next/Prev clicks) — without this, an older response can
    // resolve after a newer one and overwrite the table with the wrong
    // page's data.
    const controller = new AbortController();
    fetchRuns(controller.signal);
    return () => controller.abort();
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
