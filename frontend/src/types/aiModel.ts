/**
 * Supported AI models for running analysis - mirrors the backend's closed
 * list in `backend/app/ai/providers/factory.py:SUPPORTED_OPENAI_MODELS`.
 * Single source of truth for every model's display metadata (name, cost,
 * speed, rating, description) - `AiModelSelector` and the "Generated
 * using" result summary both read from this one catalog, nothing is
 * duplicated between them. Metadata is static/illustrative, not computed
 * dynamically - good enough for comparing trade-offs, not a billing
 * source of truth.
 */

export type AiModelId = "gpt-5.5" | "gpt-5" | "gpt-5-mini";

export interface AiModelOption {
  id: AiModelId;
  label: string;
  reasoningStars: number;
  speed: string;
  estimatedCost: string;
  bestFor: string;
  description: string;
  badge?: string;
}

export const AI_MODEL_CATALOG: AiModelOption[] = [
  {
    id: "gpt-5.5",
    label: "GPT-5.5",
    reasoningStars: 5,
    speed: "Medium",
    estimatedCost: "~₹5 / PR Analysis",
    bestFor: "Complex architecture reviews, breaking changes, migration advice",
    description:
      "Highest reasoning quality - choose this for high-risk or cross-repository changes.",
    badge: "Recommended",
  },
  {
    id: "gpt-5",
    label: "GPT-5",
    reasoningStars: 4,
    speed: "Fast",
    estimatedCost: "~₹3 / PR Analysis",
    bestFor: "General code reviews and impact analysis",
    description: "Balanced quality and cost - a solid default for most pull requests.",
    badge: "Balanced",
  },
  {
    id: "gpt-5-mini",
    label: "GPT-5-mini",
    reasoningStars: 3,
    speed: "Very Fast",
    estimatedCost: "~₹1 / PR Analysis",
    bestFor: "Routine pull requests and low-risk analysis",
    description: "Fast and economical - choose this for routine, low-risk changes.",
    badge: "Fast & Cheap",
  },
];

export const DEFAULT_AI_MODEL_ID: AiModelId = "gpt-5";

export function findAiModel(id: string): AiModelOption {
  return AI_MODEL_CATALOG.find((option) => option.id === id) ?? AI_MODEL_CATALOG[0];
}

export function starRating(count: number): string {
  return "★".repeat(count) + "☆".repeat(5 - count);
}
