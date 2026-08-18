/**
 * Types for the Engineering Task API — mirrors
 * backend/app/schemas/engineering_task.py's `EngineeringTaskResponse`
 * exactly. Read-only: no create/update/delete request types live here,
 * since creation stays API-only for this increment (see
 * EngineeringTaskDetailPage's own docstring).
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
