const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 1000 * 60 * 60 * 24 * 365],
  ["month", 1000 * 60 * 60 * 24 * 30],
  ["day", 1000 * 60 * 60 * 24],
  ["hour", 1000 * 60 * 60],
  ["minute", 1000 * 60],
];

const relativeFormatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** Formats an ISO timestamp as "3 hours ago" style relative time. */
export function formatRelativeTime(iso: string): string {
  const diffMs = new Date(iso).getTime() - Date.now();

  for (const [unit, ms] of UNITS) {
    if (Math.abs(diffMs) >= ms) {
      return relativeFormatter.format(Math.round(diffMs / ms), unit);
    }
  }
  return relativeFormatter.format(0, "minute");
}
