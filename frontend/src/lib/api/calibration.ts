import { apiFetch } from "./client";
import type { CalibrationSummary } from "../../types/calibration";

export function getCalibrationSummary(token: string): Promise<CalibrationSummary> {
  return apiFetch<CalibrationSummary>("/calibration/summary", { token });
}
