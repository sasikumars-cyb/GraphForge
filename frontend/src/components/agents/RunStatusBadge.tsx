import type { RunStatus } from "../../types/agent";
import type { StatusTone } from "../StatusBadge";
import { StatusBadge } from "../StatusBadge";

const STATUS_MAP: Record<RunStatus, { label: string; tone: StatusTone }> = {
  queued: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  completed: { label: "Completed", tone: "success" },
  partial: { label: "Partial", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  awaiting_input: { label: "Needs Input", tone: "warning" },
};

interface RunStatusBadgeProps {
  status: RunStatus;
}

/** Maps agent run status to the appropriate StatusBadge tone. */
export function RunStatusBadge({ status }: RunStatusBadgeProps) {
  const config = STATUS_MAP[status] ?? { label: status, tone: "neutral" as StatusTone };
  return <StatusBadge label={config.label} tone={config.tone} />;
}
