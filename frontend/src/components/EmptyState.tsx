import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export interface EmptyStateAction {
  label: string;
  /** Internal route. Provide either `to` or `onClick`, not both. */
  to?: string;
  onClick?: () => void;
  /** External docs link — rendered as a plain anchor with an indicator. */
  href?: string;
}

interface EmptyStateProps {
  /** What this surface is, stated as a fact: "Your architecture graph appears here". */
  title: string;
  /** One or two sentences on what will fill it and why it's useful. */
  description: string;
  /** A muted preview of the real thing — see `SampleGraph` below. */
  illustration?: ReactNode;
  actions?: EmptyStateAction[];
}

/**
 * The shared empty state.
 *
 * Every empty surface in the app used to be a single grey sentence ("No
 * data to show.", "No agent runs yet.") — a dead end, and for a product
 * whose value is invisible until data flows in, the empty state *is* the
 * onboarding. This gives each one the three things a dead end lacks: what
 * this is, a preview of what it will look like, and one thing to do next.
 *
 * The first action is styled as primary; the rest recede. Keep it to three.
 */
export function EmptyState({ title, description, illustration, actions = [] }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-line px-6 py-12 text-center">
      {illustration && (
        <div className="opacity-40" aria-hidden="true">
          {illustration}
        </div>
      )}
      <div className="max-w-md">
        <p className="text-sm font-semibold text-fg-secondary">{title}</p>
        <p className="mt-1 text-xs leading-relaxed text-fg-muted">{description}</p>
      </div>
      {actions.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {actions.map((action, i) => (
            <EmptyStateButton key={action.label} action={action} primary={i === 0} />
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyStateButton({ action, primary }: { action: EmptyStateAction; primary: boolean }) {
  const className = primary
    ? "focus-ring inline-flex items-center rounded-md bg-accent-solid px-3.5 py-1.5 text-xs font-semibold text-accent-on-solid shadow-xs transition-colors hover:brightness-110"
    : "focus-ring inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover";

  if (action.href) {
    return (
      <a href={action.href} target="_blank" rel="noreferrer" className={className}>
        {action.label} ↗
      </a>
    );
  }
  if (action.to) {
    return (
      <Link to={action.to} className={className}>
        {action.label}
      </Link>
    );
  }
  return (
    <button type="button" onClick={action.onClick} className={className}>
      {action.label}
    </button>
  );
}

/** A six-node dependency graph, drawn flat — the shape of what Architecture
 * fills with once a repository is indexed. Decorative; the surrounding
 * EmptyState marks it `aria-hidden`. */
export function SampleGraph() {
  return (
    <svg width="220" height="96" viewBox="0 0 220 96" fill="none">
      <g stroke="currentColor" strokeWidth="1" className="text-fg-muted">
        <line x1="62" y1="24" x2="92" y2="24" />
        <line x1="152" y1="24" x2="182" y2="48" />
        <line x1="62" y1="72" x2="92" y2="30" />
        <line x1="122" y1="36" x2="122" y2="60" />
      </g>
      <g className="text-fg-muted" fill="currentColor" fillOpacity="0.15" stroke="currentColor">
        <rect x="8" y="12" width="54" height="24" rx="5" />
        <rect x="8" y="60" width="54" height="24" rx="5" />
        <rect x="92" y="12" width="60" height="24" rx="5" />
        <rect x="92" y="60" width="60" height="24" rx="5" />
        <rect x="182" y="36" width="30" height="24" rx="5" />
      </g>
    </svg>
  );
}

/** A four-stage pipeline — the shape of a workflow run. */
export function SamplePipeline() {
  return (
    <svg width="240" height="40" viewBox="0 0 240 40" fill="none">
      <g className="text-fg-muted" stroke="currentColor" strokeWidth="1">
        <line x1="46" y1="20" x2="62" y2="20" />
        <line x1="110" y1="20" x2="126" y2="20" />
        <line x1="174" y1="20" x2="190" y2="20" />
      </g>
      <g className="text-fg-muted" fill="currentColor" fillOpacity="0.15" stroke="currentColor">
        <rect x="2" y="6" width="44" height="28" rx="6" />
        <rect x="62" y="6" width="48" height="28" rx="6" />
        <rect x="126" y="6" width="48" height="28" rx="6" />
        <rect x="190" y="6" width="48" height="28" rx="6" />
      </g>
    </svg>
  );
}

/** Ascending bars — the shape of the metrics dashboard. */
export function SampleChart() {
  return (
    <svg width="200" height="80" viewBox="0 0 200 80" fill="none">
      <g className="text-fg-muted" fill="currentColor" fillOpacity="0.2">
        {[28, 44, 34, 58, 48, 68, 62].map((h, i) => (
          <rect key={i} x={4 + i * 28} y={72 - h} width="18" height={h} rx="2" />
        ))}
      </g>
      <line x1="0" y1="72" x2="200" y2="72" className="text-fg-muted" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}
