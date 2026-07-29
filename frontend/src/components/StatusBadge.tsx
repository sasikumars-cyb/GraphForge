export type StatusTone = "neutral" | "info" | "success" | "warning" | "serious" | "danger";

interface StatusBadgeProps {
  label: string;
  tone: StatusTone;
}

/**
 * Tone -> semantic role. Every tone reads its three values from one status
 * family, so a tone can never end up with a foreground borrowed from a
 * different hue than its fill — which is how the old pairing of a pale amber
 * ink over a 10%-amber tint ended up at 1.02:1 in the light themes.
 */
// Ring opacity was a flat 40% across every theme — in High Contrast, whose
// whole premise is "saturated accents for maximum readability", that left
// the badge's own boundary the one place still washed out even though its
// text already hits AAA contrast. 70% keeps Light/Dark/Midnight/Modern Blue
// looking the same (a soft outline was already the intent there) while
// giving High Contrast's much more saturated `-line` colors an outline that
// actually reads as bold.
const TONE_STYLES: Record<StatusTone, string> = {
  neutral: "bg-neutral-bg text-fg-muted ring-neutral-line/70",
  info: "bg-info-bg text-info-fg ring-info-line/70",
  success: "bg-success-bg text-success-fg ring-success-line/70",
  warning: "bg-warning-bg text-warning-fg ring-warning-line/70",
  serious: "bg-serious-bg text-serious-fg ring-serious-line/70",
  danger: "bg-danger-bg text-danger-fg ring-danger-line/70",
};

/**
 * Generic status pill. Domain-specific status strings (pull request state,
 * repository health, report status, ...) each map to one of the tones at the
 * call site — this component knows nothing about any domain.
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
