import { ArrowRight, Loader2 } from "lucide-react";

const STAGE_ACTIONS: Record<string, string> = {
  development: "Continue to Development",
  testing: "Generate Test Plan",
  review: "Start Review",
  completed: "Workflow Complete",
};

interface StageNavigationProps {
  nextStage: string | null;
  isSubmitting: boolean;
  onContinue: () => void;
}

/** Contextual action button shown after a stage completes successfully. */
export function StageNavigation({ nextStage, isSubmitting, onContinue }: StageNavigationProps) {
  if (!nextStage || nextStage === "completed") {
    return (
      <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
        <p className="text-sm font-medium text-emerald-300">
          All SDLC stages complete. This workflow is ready for release.
        </p>
      </div>
    );
  }

  const label = STAGE_ACTIONS[nextStage] ?? `Continue to ${nextStage}`;

  return (
    <button
      type="button"
      onClick={onContinue}
      disabled={isSubmitting}
      className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
      aria-label={label}
    >
      {isSubmitting ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
      )}
      {isSubmitting ? "Processing…" : label}
    </button>
  );
}
