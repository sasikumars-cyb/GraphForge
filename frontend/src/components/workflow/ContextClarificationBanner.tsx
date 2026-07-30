import { useState } from "react";
import { HelpCircle, Loader2, Search } from "lucide-react";
import type { PendingClarification } from "../../types/agent";

interface ContextClarificationBannerProps {
  pendingClarification: PendingClarification;
  isSubmitting: boolean;
  onAnswer: (questionId: string, answer: string) => void;
}

/** Context Discovery exhausted every provider it could use and one genuine
 * ambiguity remains, so it paused instead of guessing.
 *
 * Two things this component must get right, both learned from the previous
 * version:
 *
 * - **Options are values, not verbs.** The backend only ever sends real
 *   candidate values here (repository names the graph actually contains);
 *   remediation like "Connect Jira" is rendered in the Context Explorer as
 *   guidance, never as a clickable answer. Clicking an instruction label used
 *   to submit that literal string as the answer, which the engine then treated
 *   as a repository name. The label below makes the contract visible to the
 *   user too.
 * - **Show what was already tried.** A question with no visible effort behind
 *   it reads as the system's first move. `investigated` is the engine's own
 *   record of the automated avenues it spent before asking. */
export function ContextClarificationBanner({
  pendingClarification,
  isSubmitting,
  onAnswer,
}: ContextClarificationBannerProps) {
  const [freeText, setFreeText] = useState("");
  const hasOptions = pendingClarification.options.length > 0;
  const investigated = pendingClarification.investigated ?? [];

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-warning-line/40 bg-warning-bg px-5 py-4">
      <div className="flex items-start gap-3">
        <HelpCircle className="mt-0.5 h-5 w-5 shrink-0 text-warning-fg" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-semibold text-warning-fg">{pendingClarification.question}</p>
          <p className="text-xs text-warning-fg/80">{pendingClarification.why}</p>
        </div>
      </div>

      {investigated.length > 0 && (
        <details className="pl-8">
          <summary className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-warning-fg/80 hover:text-warning-fg">
            <Search className="h-3.5 w-3.5" aria-hidden="true" />I tried {investigated.length} thing
            {investigated.length === 1 ? "" : "s"} first
          </summary>
          <ul className="mt-1.5 flex flex-col gap-0.5">
            {investigated.map((line, i) => (
              <li key={i} className="text-xs text-warning-fg/70">
                {line}
              </li>
            ))}
          </ul>
        </details>
      )}

      {hasOptions ? (
        <div className="flex flex-col gap-1.5 pl-8">
          <p className="text-xs font-medium text-warning-fg/70">Pick one:</p>
          <div className="flex flex-wrap gap-2">
            {pendingClarification.options.map((option) => (
              <button
                key={option}
                type="button"
                disabled={isSubmitting}
                onClick={() => onAnswer(pendingClarification.question_id, option)}
                className="focus-ring inline-flex items-center gap-1.5 rounded-md bg-surface px-3 py-2 font-mono text-xs font-medium text-fg-secondary ring-1 ring-inset ring-warning-line/40 transition-colors hover:bg-warning-bg hover:text-warning-fg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {option}
              </button>
            ))}
          </div>
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

      <p className="pl-8 text-xs text-warning-fg/60">
        I'll verify your answer against the knowledge graph before relying on it.
      </p>
    </div>
  );
}
