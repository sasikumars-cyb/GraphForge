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
