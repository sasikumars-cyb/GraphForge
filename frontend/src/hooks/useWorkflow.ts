/**
 * Hook to manage workflow lifecycle — create, continue, and poll.
 */

import { useCallback, useRef, useState } from "react";
import { useAuth } from "../app/auth-context";
import { createWorkflow, continueWorkflow, getWorkflow } from "../lib/api/workflows";
import type { WorkflowDetail } from "../types/agent";

interface UseWorkflowReturn {
  workflow: WorkflowDetail | null;
  isSubmitting: boolean;
  error: string | null;
  create: (title: string) => Promise<void>;
  continueToNext: () => Promise<void>;
  refresh: () => Promise<void>;
  reset: () => void;
}

export function useWorkflow(workflowId?: string): UseWorkflowReturn {
  const { token } = useAuth();
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currentIdRef = useRef<string | null>(workflowId ?? null);

  const refresh = useCallback(async () => {
    if (!token || !currentIdRef.current) return;
    try {
      const detail = await getWorkflow(token, currentIdRef.current);
      setWorkflow(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load workflow.");
    }
  }, [token]);

  const create = useCallback(
    async (title: string) => {
      if (!token) {
        setError("Not authenticated.");
        return;
      }

      setIsSubmitting(true);
      setError(null);
      setWorkflow(null);

      try {
        const response = await createWorkflow(token, { title });
        currentIdRef.current = response.workflow_id;
        // Fetch full workflow detail
        const detail = await getWorkflow(token, response.workflow_id);
        setWorkflow(detail);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create workflow.");
      } finally {
        setIsSubmitting(false);
      }
    },
    [token],
  );

  const continueToNext = useCallback(async () => {
    if (!token || !currentIdRef.current) {
      setError("No active workflow.");
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
      await continueWorkflow(token, currentIdRef.current);
      // Refresh to get updated state
      const detail = await getWorkflow(token, currentIdRef.current);
      setWorkflow(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to continue workflow.");
    } finally {
      setIsSubmitting(false);
    }
  }, [token]);

  const reset = useCallback(() => {
    currentIdRef.current = null;
    setWorkflow(null);
    setError(null);
    setIsSubmitting(false);
  }, []);

  return { workflow, isSubmitting, error, create, continueToNext, refresh, reset };
}
