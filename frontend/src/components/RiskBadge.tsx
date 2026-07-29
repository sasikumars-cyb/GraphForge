import type { RiskLevel } from "../types/domain";

interface RiskBadgeProps {
  level: RiskLevel;
}

/**
 * Risk is an *ordered* four-step severity scale, so it reads from the four
 * reserved status roles rather than four unrelated categorical hues. That
 * keeps "critical" the same red as every other error surface in the app and
 * keeps the four steps monotonic in weight, in every theme.
 */
const RISK_STYLES: Record<RiskLevel, { label: string; badge: string; dot: string }> = {
  critical: {
    label: "Critical",
    badge: "bg-danger-bg text-danger-fg ring-danger-line/40",
    dot: "bg-danger-solid",
  },
  high: {
    label: "High",
    badge: "bg-serious-bg text-serious-fg ring-serious-line/40",
    dot: "bg-serious-solid",
  },
  medium: {
    label: "Medium",
    badge: "bg-warning-bg text-warning-fg ring-warning-line/40",
    dot: "bg-warning-solid",
  },
  low: {
    label: "Low",
    badge: "bg-success-bg text-success-fg ring-success-line/40",
    dot: "bg-success-solid",
  },
};

/**
 * Dedicated risk-level indicator — deliberately separate from StatusBadge
 * since risk is the one signal every page in GraphForge has to make
 * scannable at a glance. Pairs color with a dot and a word, never color
 * alone, so the level survives greyscale and colour-vision deficiency.
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
