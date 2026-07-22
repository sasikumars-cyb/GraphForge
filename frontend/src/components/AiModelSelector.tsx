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
    <div className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor="ai-model-select" className="text-xs font-medium text-slate-400">
          AI model
        </label>
        <select
          id="ai-model-select"
          value={modelId}
          onChange={(event) => setModelId(event.target.value as typeof modelId)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
        >
          {AI_MODEL_CATALOG.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{selected.label}</span>
        {selected.badge && (
          <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-400">
            {selected.badge}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-slate-500">Provider</dt>
          <dd className="text-slate-300">OpenAI</dd>
        </div>
        <div>
          <dt className="text-slate-500">Reasoning Quality</dt>
          <dd className="tracking-wide text-amber-400">{starRating(selected.reasoningStars)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Speed</dt>
          <dd className="text-slate-300">{selected.speed}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Estimated Cost</dt>
          <dd className="text-slate-300">{selected.estimatedCost}</dd>
        </div>
        <div className="col-span-2 sm:col-span-2">
          <dt className="text-slate-500">Best For</dt>
          <dd className="text-slate-300">{selected.bestFor}</dd>
        </div>
      </dl>

      <p className="border-t border-slate-800 pt-2 text-xs text-slate-400">
        {selected.description}
      </p>
    </div>
  );
}
