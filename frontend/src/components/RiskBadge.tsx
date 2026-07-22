import type { RiskLevel } from "../types/domain";

interface RiskBadgeProps {
  level: RiskLevel;
}

const RISK_STYLES: Record<RiskLevel, { label: string; badge: string; dot: string }> = {
  critical: {
    label: "Critical",
    badge: "bg-red-500/10 text-red-300 ring-red-500/30",
    dot: "bg-red-400",
  },
  high: {
    label: "High",
    badge: "bg-orange-500/10 text-orange-300 ring-orange-500/30",
    dot: "bg-orange-400",
  },
  medium: {
    label: "Medium",
    badge: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
    dot: "bg-amber-400",
  },
  low: {
    label: "Low",
    badge: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
    dot: "bg-emerald-400",
  },
};

/**
 * Dedicated risk-level indicator — deliberately separate from StatusBadge
 * since risk is the one signal every page in ChangeGuard has to make
 * scannable at a glance. Pairs color with a dot, not color alone.
 */
export function RiskBadge({ level }: RiskBadgeProps) {
  const style = RISK_STYLES[level];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style.badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {style.label}
    </span>
  );
}
