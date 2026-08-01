import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import { getMetricsReport } from "../lib/api/metrics";
import type { MetricsReportResponse, MetricsScope } from "../types/metrics";

export interface ReportsData {
  report: MetricsReportResponse | null;
  scope: MetricsScope;
  setScope: (scope: MetricsScope) => void;
  isLoading: boolean;
  error: string | null;
  refresh: () => void;
}

/** Live data for the Reports dashboard — fetched once on mount/scope change,
 * and again only on explicit `refresh()` (no polling, per V1 scope: manual
 * refresh only). */
export function useReportsData(): ReportsData {
  const { token } = useAuth();
  const [scope, setScope] = useState<MetricsScope>("user");
  const [report, setReport] = useState<MetricsReportResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(() => setRefreshTick((tick) => tick + 1), []);

  useEffect(() => {
    if (!token) {
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);

    getMetricsReport(token, { scope, window_days: 30 }, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setReport(data);
        setIsLoading(false);
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Failed to load reports data.");
        setIsLoading(false);
      });

    return () => controller.abort();
  }, [token, scope, refreshTick]);

  return { report, scope, setScope, isLoading, error, refresh };
}
