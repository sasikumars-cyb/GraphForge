import { createContext, useContext } from "react";
import type { AiModelId } from "../types/aiModel";

export interface AiModelContextValue {
  modelId: AiModelId;
  setModelId: (modelId: AiModelId) => void;
}

export const AiModelContext = createContext<AiModelContextValue | undefined>(undefined);

export function useAiModel(): AiModelContextValue {
  const context = useContext(AiModelContext);
  if (context === undefined) {
    throw new Error("useAiModel must be used within an AiModelProvider");
  }
  return context;
}
