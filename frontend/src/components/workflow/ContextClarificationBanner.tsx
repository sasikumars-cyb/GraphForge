import { useState } from "react";
import { HelpCircle, Loader2 } from "lucide-react";
import type { PendingClarification } from "../../types/agent";

interface ContextClarificationBannerProps {
  pendingClarification: PendingClarification;
  isSubmitting: boolean;
  onAnswer: (questionId: string, answer: string) => void;
}

/** Context Discovery's reasoning loop hit a genuine blocking ambiguity
 * (repository can't be determined, two repositories tie, a Jira reference
 * didn't resolve — see reasoning_loop.py) and paused instead of guessing.
 * Styled like WorkflowApprovalBanner/ApprovalGateBanner but distinct: this
 * is a question with an answer, not an approve/reject decision. `options`
 * renders as buttons; an empty list falls back to free text. */
export function ContextClarificationBanner({
  pendingClarification,
  isSubmitting,
  onAnswer,
}: ContextClarificationBannerProps) {
  const [freeText, setFreeText] = useState("");
  const hasOptions = pendingClarification.options.length > 0;

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-warning-line/40 bg-warning-bg px-5 py-4">
      <div className="flex items-start gap-3">
        <HelpCircle className="mt-0.5 h-5 w-5 shrink-0 text-warning-fg" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-warning-fg">{pendingClarification.question}</p>
          <p className="text-xs text-warning-fg/80">{pendingClarification.why}</p>
        </div>
      </div>

      {hasOptions ? (
        <div className="flex flex-wrap gap-2 pl-8">
          {pendingClarification.options.map((option) => (
            <button
              key={option}
              type="button"
              disabled={isSubmitting}
              onClick={() => onAnswer(pendingClarification.question_id, option)}
              className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-surface px-3 py-2 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-warning-line/40 transition-colors hover:bg-warning-bg hover:text-warning-fg disabled:cursor-not-allowed disabled:opacity-50"
            >
              {option}
            </button>
          ))}
        </div>
      ) : (
        <form
          className="flex flex-col gap-2 pl-8 sm:flex-row sm:items-center"
          onSubmit={(e) => {
            e.preventDefault();
            const trimmed = freeText.trim();
            if (!trimmed) return;
            onAnswer(pendingClarification.question_id, trimmed);
          }}
        >
          <input
            type="text"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            disabled={isSubmitting}
            placeholder="Type your answer…"
            className="w-full flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm text-fg placeholder-fg-subtle focus:border-accent-line disabled:opacity-50 sm:max-w-md"
          />
          <button
            type="submit"
            disabled={isSubmitting || !freeText.trim()}
            className="focus-ring inline-flex shrink-0 items-center gap-1.5 rounded-md bg-accent-solid px-4 py-2 text-xs font-semibold text-accent-on-solid shadow-xs transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            {isSubmitting ? "Submitting…" : "Answer"}
          </button>
        </form>
      )}
    </div>
  );
}
