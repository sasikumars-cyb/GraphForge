import { Loader2, CheckCircle2, XCircle, Clock, AlertTriangle } from "lucide-react";
import type { RunStatus } from "../../types/agent";

interface RunProgressProps {
  status: RunStatus;
  error?: string | null;
}

const STATUS_CONFIG: Record<
  RunStatus,
  { icon: typeof Loader2; label: string; color: string; animate?: boolean }
> = {
  queued: { icon: Clock, label: "Queued — waiting to start…", color: "text-fg-muted" },
  running: { icon: Loader2, label: "Running — agent is executing…", color: "text-info-fg", animate: true },
  completed: { icon: CheckCircle2, label: "Completed", color: "text-success-fg" },
  partial: { icon: AlertTriangle, label: "Partial — completed with issues", color: "text-warning-fg" },
  failed: { icon: XCircle, label: "Failed", color: "text-danger-fg" },
};

/** A compact status indicator for in-progress or completed runs. */
export function RunProgress({ status, error }: RunProgressProps) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.queued;
  const Icon = config.icon;

  return (
    <div className="flex items-center gap-3" role="status" aria-live="polite" aria-label={`Run status: ${config.label}`}>
      <Icon
        className={`h-5 w-5 ${config.color} ${config.animate ? "animate-spin" : ""}`}
        aria-hidden="true"
      />
      <div>
        <p className={`text-sm font-medium ${config.color}`}>{config.label}</p>
        {error && (
          <p className="mt-0.5 text-xs text-danger-fg">{error}</p>
        )}
      </div>
    </div>
  );
}
