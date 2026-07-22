export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

interface StatusBadgeProps {
  label: string;
  tone: StatusTone;
}

const TONE_STYLES: Record<StatusTone, string> = {
  neutral: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
  info: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  success: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  warning: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  danger: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
};

/**
 * Generic status pill. Domain-specific status strings (pull request state,
 * repository health, report status, ...) each map to one of the five tones
 * at the call site — this component knows nothing about any domain.
 */
export function StatusBadge({ label, tone }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_STYLES[tone]}`}
    >
      {label}
    </span>
  );
}
