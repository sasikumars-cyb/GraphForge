import { useAiModel } from "../app/ai-model-context";
import { AI_MODEL_CATALOG, findAiModel, starRating } from "../types/aiModel";

/**
 * Lets the user pick which supported model the next AI analysis run uses,
 * and shows a professional-looking config card for it. Placed inside the
 * "AI analysis" panel (PullRequestDetailPage) - the point where the
 * choice actually matters, not buried in Settings.
 */
export function AiModelSelector() {
  const { modelId, setModelId } = useAiModel();
  const selected = findAiModel(modelId);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line-muted bg-canvas p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor="ai-model-select" className="text-xs font-medium text-fg-muted">
          AI model
        </label>
        <select
          id="ai-model-select"
          value={modelId}
          onChange={(event) => setModelId(event.target.value as typeof modelId)}
          className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-fg-secondary focus:border-info-line "
        >
          {AI_MODEL_CATALOG.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-fg">{selected.label}</span>
        {selected.badge && (
          <span className="rounded-full bg-info-bg px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-info-fg">
            {selected.badge}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-fg-muted">Provider</dt>
          <dd className="text-fg-secondary">OpenAI</dd>
        </div>
        <div>
          <dt className="text-fg-muted">Reasoning Quality</dt>
          <dd className="tracking-wide text-warning-fg">{starRating(selected.reasoningStars)}</dd>
        </div>
        <div>
          <dt className="text-fg-muted">Speed</dt>
          <dd className="text-fg-secondary">{selected.speed}</dd>
        </div>
        <div>
          <dt className="text-fg-muted">Estimated Cost</dt>
          <dd className="text-fg-secondary">{selected.estimatedCost}</dd>
        </div>
        <div className="col-span-2 sm:col-span-2">
          <dt className="text-fg-muted">Best For</dt>
          <dd className="text-fg-secondary">{selected.bestFor}</dd>
        </div>
      </dl>

      <p className="border-t border-line-muted pt-2 text-xs text-fg-muted">
        {selected.description}
      </p>
    </div>
  );
}
