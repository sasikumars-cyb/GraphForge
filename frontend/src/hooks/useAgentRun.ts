/**
 * Hook that manages the lifecycle of creating and polling an agent run.
 * Handles: submit → create run → poll for completion → return result.
 *
 * The polling loop is a useEffect keyed on runId, not the old manual
 * `while` loop that lived inside `submit()` — that loop had no unmount
 * cleanup at all (not even a `setInterval` handle to clear), so a
 * component using this hook that unmounted mid-run left it running: it
 * kept calling getAgentRun and calling React state setters on an
 * unmounted component every 2s until the run finished or the 2-minute
 * cap was hit. A useEffect's cleanup function always runs on unmount,
 * which is what actually stops it here — the aborted fetch and cleared
 * timeout below are just how that cleanup makes the in-flight request/next
 * tick a no-op, so no request or setState fires afterward.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../app/auth-context";
import { createAgentRun, getAgentRun } from "../lib/api/agentRuns";
import type { CreateRunRequest, RunDetail } from "../types/agent";

interface UseAgentRunReturn {
  run: RunDetail | null;
  isSubmitting: boolean;
  error: string | null;
  submit: (request: CreateRunRequest) => Promise<void>;
  reset: () => void;
}

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_MS = 2 * 60 * 1000; // 2 minutes — matches the previous maxAttempts * interval

export function useAgentRun(): UseAgentRunReturn {
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const pollStartRef = useRef<number>(0);

  const submit = useCallback(
    async (request: CreateRunRequest) => {
      if (!token) {
        setError("Not authenticated.");
        return;
      }

      setIsSubmitting(true);
      setError(null);
      setRun(null);
      setRunId(null);

      try {
        const createResponse = await createAgentRun(token, request);
        pollStartRef.current = Date.now();
        // Triggers the polling effect below, which does the first fetch
        // immediately (the run may already be complete for fast agents).
        setRunId(createResponse.run_id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "An unexpected error occurred.");
        setIsSubmitting(false);
      }
    },
    [token],
  );

  useEffect(() => {
    if (!token || !runId) return;

    let cancelled = false;
    let timeoutId: number | undefined;
    const controller = new AbortController();

    const poll = async () => {
      try {
        const detail = await getAgentRun(token, runId, controller.signal);
        if (cancelled) return;
        setRun(detail);

        if (detail.status !== "queued" && detail.status !== "running") {
          setIsSubmitting(false);
          return;
        }
        if (Date.now() - pollStartRef.current > MAX_POLL_MS) {
          setError("Run is taking longer than expected. Check run history for results.");
          setIsSubmitting(false);
          return;
        }
        timeoutId = window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(err instanceof Error ? err.message : "An unexpected error occurred.");
        setIsSubmitting(false);
      }
    };

    poll();

    return () => {
      cancelled = true;
      controller.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [token, runId]);

  const reset = useCallback(() => {
    setRun(null);
    setRunId(null);
    setError(null);
    setIsSubmitting(false);
  }, []);

  return { run, isSubmitting, error, submit, reset };
}
