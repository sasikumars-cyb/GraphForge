/**
 * Types for the Engineering Task API — mirrors
 * backend/app/schemas/engineering_task.py exactly (`EngineeringTaskResponse`,
 * `EngineeringTaskSummary`, `CreateEngineeringTaskRequest`).
 *
 * Phase 7.2 productizes creation and listing in the UI — `CreateEngineeringTaskInput`
 * is genuinely new, but still mirrors the existing, unmodified POST body exactly;
 * no new backend request shape was introduced for this.
 */

export interface EngineeringTaskGoal {
  description: string;
  postconditions: string[];
}

export interface EngineeringTaskPlanStep {
  event_id: string;
  description: string;
  postcondition: string;
  invalidated: boolean;
}

export interface EngineeringTaskObservation {
  success: boolean | null;
  outcome: string | null;
  classification: string | null;
  actor: string | null;
}

export interface EngineeringTask {
  task_id: string;
  created_at: string;
  goal_event_id: string;
  goal: EngineeringTaskGoal;
  plan_event_id: string;
  plan_step_event_id: string;
  plan_step: EngineeringTaskPlanStep | null;
  generator_observation: EngineeringTaskObservation;
  verifier_observation: EngineeringTaskObservation;
}

/** One row of `GET /engineering-tasks` — mirrors `EngineeringTaskSummary`. */
export interface EngineeringTaskSummary {
  task_id: string;
  created_at: string;
  updated_at: string;
  description: string;
  classification: string | null;
}

/** Mirrors `CreateEngineeringTaskRequest` exactly — the existing, unmodified
 * POST body. `postconditions` is required by that existing schema (min length
 * 1), not an addition invented for this form. */
export interface CreateEngineeringTaskInput {
  description: string;
  postconditions: string[];
}
