/**
 * Hook that manages the lifecycle of creating and polling an agent run.
 * Handles: submit → create run → poll for completion → return result.
 */

import { useCallback, useRef, useState } from "react";
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

export function useAgentRun(): UseAgentRunReturn {
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef(false);

  const submit = useCallback(
    async (request: CreateRunRequest) => {
      if (!token) {
        setError("Not authenticated.");
        return;
      }

      setIsSubmitting(true);
      setError(null);
      setRun(null);
      abortRef.current = false;

      try {
        const createResponse = await createAgentRun(token, request);
        const runId = createResponse.run_id;

        // Fetch the full run detail (may already be complete for fast agents)
        let detail = await getAgentRun(token, runId);
        setRun(detail);

        // Poll if still running
        let attempts = 0;
        const maxAttempts = 60; // 2 minutes at 2s intervals
        while (
          (detail.status === "queued" || detail.status === "running") &&
          attempts < maxAttempts &&
          !abortRef.current
        ) {
          await new Promise((resolve) => setTimeout(resolve, 2000));
          detail = await getAgentRun(token, runId);
          setRun(detail);
          attempts++;
        }

        if (attempts >= maxAttempts && detail.status === "running") {
          setError("Run is taking longer than expected. Check run history for results.");
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "An unexpected error occurred.";
        setError(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [token],
  );

  const reset = useCallback(() => {
    abortRef.current = true;
    setRun(null);
    setError(null);
    setIsSubmitting(false);
  }, []);

  return { run, isSubmitting, error, submit, reset };
}
