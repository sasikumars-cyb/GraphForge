/**
 * Shared presentation logic for an Engineering Task's Observation
 * classification — used by both EngineeringTaskListPage and
 * EngineeringTaskDetailPage so the two views can never describe the same
 * classification differently.
 *
 * Deliberately NOT a "Goal Satisfied" / "succeeded" / "failed" label —
 * per docs/graphforge/CAPABILITIES_CONTROL_PLANE_ARCHITECTURE.md §16-17,
 * `Expected` classification is a fact about ONE Observation, not proof the
 * Goal was satisfied (Goal Satisfied is a separate, four-part Control
 * Plane predicate this API does not currently compute or expose). Labeling
 * this "Succeeded" would fabricate a claim Engineering State doesn't make.
 * These labels describe the VERIFIER's classification honestly, as
 * classification — nothing more.
 */

import type { StatusTone } from "../components/StatusBadge";

export interface ClassificationPresentation {
  label: string;
  tone: StatusTone;
  explanation: string;
}

const PRESENTATIONS: Record<string, ClassificationPresentation> = {
  expected: {
    label: "Verified as expected",
    tone: "success",
    explanation: "The predicted outcome was confirmed by Independent Verification.",
  },
  anomaly: {
    label: "Anomaly",
    tone: "warning",
    explanation:
      "Something unexpected happened at the infrastructure/transport level — the Action itself may not have run as intended.",
  },
  contradiction: {
    label: "Contradiction",
    tone: "danger",
    explanation: "The actual outcome directly contradicted what was predicted.",
  },
  uncertain_outcome: {
    label: "Uncertain outcome",
    tone: "warning",
    explanation: "The outcome could not be conclusively evaluated either way.",
  },
};

const PENDING: ClassificationPresentation = {
  label: "Pending",
  tone: "neutral",
  explanation: "No verified outcome is recorded for this task yet.",
};

export function classificationPresentation(
  classification: string | null,
): ClassificationPresentation {
  if (classification === null) return PENDING;
  return PRESENTATIONS[classification] ?? { label: classification, tone: "neutral", explanation: "" };
}
