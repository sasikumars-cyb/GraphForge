import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../app/auth-context";
import {
  getRepositoriesOverview,
  type GetRepositoriesOverviewParams,
  type RepositoryOverviewItem,
  type RepositoryOverviewStats,
} from "../lib/api/repositories";

export type { RepositoryOverviewItem, RepositoryOverviewStats };

const EMPTY_STATS: RepositoryOverviewStats = {
  repositories_monitored: 0,
  organization_count: 0,
  open_pull_request_count: 0,
  awaiting_analysis_count: 0,
  high_risk_this_week_count: 0,
  avg_indexing_time_ms: null,
};

export interface RepositoriesOverview {
  /** Just the requested page — never the whole account. */
  items: RepositoryOverviewItem[];
  /** Account-wide, unaffected by the active filters or page. */
  stats: RepositoryOverviewStats;
  /** Rows matching the active filters, across all pages. */
  total: number;
  hasMore: boolean;
  isLoading: boolean;
  error: string | null;
  refetch: () => void;
}

export function formatIndexingDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return "<1s";
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  return `${Math.floor(totalSeconds / 60)}m ${totalSeconds % 60}s`;
}

/** One request per page of the Repositories list — health, open-PR counts
 * and indexing status are all derived server-side (see the backend's
 * `GET /repositories/overview`).
 *
 * This deliberately keeps `stats` and `items` together in a single
 * response: they're computed from the same snapshot, so a filter change
 * can never leave the headline numbers describing a different moment than
 * the list under them. */
export function useRepositoriesOverview(params: GetRepositoriesOverviewParams) {
  const { token } = useAuth();
  const { page, pageSize, q, indexing, health } = params;
  const [state, setState] = useState<Omit<RepositoriesOverview, "refetch">>({
    items: [],
    stats: EMPTY_STATS,
    total: 0,
    hasMore: false,
    isLoading: true,
    error: null,
  });
  // Bumped by `refetch` to re-run the effect below without duplicating its
  // request/abort handling in a second code path.
  const [reloadToken, setReloadToken] = useState(0);
  const refetch = useCallback(() => setReloadToken((n) => n + 1), []);

  // A filter/page change must not flash the whole page back to skeletons —
  // it re-fetches with the previous rows still on screen, showing them as
  // stale instead. Only the very first load has nothing to show.
  const hasLoadedRef = useRef(false);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setState((prev) => ({ ...prev, isLoading: !hasLoadedRef.current || prev.error !== null }));
    (async () => {
      try {
        const data = await getRepositoriesOverview(
          token,
          { page, pageSize, q, indexing, health },
          controller.signal,
        );
        hasLoadedRef.current = true;
        setState({
          items: data.items,
          stats: data.stats,
          total: data.total,
          hasMore: data.has_more,
          isLoading: false,
          error: null,
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error: err instanceof Error ? err.message : "Failed to load repositories.",
        }));
      }
    })();
    return () => controller.abort();
  }, [token, page, pageSize, q, indexing, health, reloadToken]);

  return { ...state, refetch };
}

/** Delays propagating `value` until it has stopped changing for `delayMs` —
 * so typing in the search box issues one request per pause, not one per
 * keystroke. */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
