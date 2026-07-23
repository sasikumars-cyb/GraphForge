import { useState, type ReactNode } from "react";
import { AI_MODEL_CATALOG, DEFAULT_AI_MODEL_ID, type AiModelId } from "../types/aiModel";
import { AiModelContext } from "./ai-model-context";

const MODEL_STORAGE_KEY = "graphforge.aiModel";

function isSupportedModelId(value: string | null): value is AiModelId {
  return AI_MODEL_CATALOG.some((option) => option.id === value);
}

/**
 * Session-scoped (sessionStorage, not localStorage): the model choice is a
 * per-visit demo preference, not an account setting like the auth token -
 * it should reset when the browser tab/session ends, not persist forever.
 */
export function AiModelProvider({ children }: { children: ReactNode }) {
  const [modelId, setModelIdState] = useState<AiModelId>(() => {
    const stored = sessionStorage.getItem(MODEL_STORAGE_KEY);
    return isSupportedModelId(stored) ? stored : DEFAULT_AI_MODEL_ID;
  });

  function setModelId(next: AiModelId) {
    sessionStorage.setItem(MODEL_STORAGE_KEY, next);
    setModelIdState(next);
  }

  return (
    <AiModelContext.Provider value={{ modelId, setModelId }}>{children}</AiModelContext.Provider>
  );
}
