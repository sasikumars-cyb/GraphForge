/** Shared formatters for the Metrics section (overview report and the
 * per-workflow LLM usage breakdown) - kept in one place so the two pages
 * present numbers identically rather than drifting apart. */

export function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}

export function formatLabel(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** `2026-08-07` → `08-07`, for chart axes on a date series.
 *
 * Lives here rather than in SimpleCharts because it is a *caller's* choice:
 * the axis previously trimmed five characters off every label
 * unconditionally, which silently mangled the repository names the same
 * chart renders elsewhere. Non-date input is returned untouched. */
export function shortenIsoDate(label: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(label) ? label.slice(5) : label;
}
