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
  queued: { icon: Clock, label: "Queued — waiting to start…", color: "text-slate-400" },
  running: { icon: Loader2, label: "Running — agent is executing…", color: "text-sky-400", animate: true },
  completed: { icon: CheckCircle2, label: "Completed", color: "text-emerald-400" },
  partial: { icon: AlertTriangle, label: "Partial — completed with issues", color: "text-amber-400" },
  failed: { icon: XCircle, label: "Failed", color: "text-rose-400" },
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
          <p className="mt-0.5 text-xs text-rose-300">{error}</p>
        )}
      </div>
    </div>
  );
}
