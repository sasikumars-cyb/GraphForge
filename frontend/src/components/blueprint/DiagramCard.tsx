/**
 * DiagramCard — single diagram with header, confidence badge, fullscreen toggle,
 * entrance animation, and hover elevation.
 *
 * Purposefully minimal: all visual complexity lives in BlueprintRenderer.
 * This component owns layout, title display, badges, and the fullscreen overlay.
 */

import { useState, useEffect, useRef, type ReactNode } from "react";
import { Maximize2, Minimize2, Download } from "lucide-react";
import type { Diagram } from "../../types/blueprint";
import { BlueprintRenderer } from "./BlueprintRenderer";

const DIAGRAM_TYPE_LABELS: Record<string, string> = {
  flow: "Flow",
  architecture: "Architecture",
  sequence: "Sequence",
  dependency: "Dependency",
  timeline: "Timeline",
  er: "Entity Relationship",
  risk_heatmap: "Risk Heatmap",
};

const DIAGRAM_TYPE_COLORS: Record<string, string> = {
  flow: "text-sky-400 bg-sky-500/10 ring-sky-500/30",
  architecture: "text-violet-400 bg-violet-500/10 ring-violet-500/30",
  sequence: "text-teal-400 bg-teal-500/10 ring-teal-500/30",
  dependency: "text-amber-400 bg-amber-500/10 ring-amber-500/30",
  timeline: "text-blue-400 bg-blue-500/10 ring-blue-500/30",
  er: "text-pink-400 bg-pink-500/10 ring-pink-500/30",
  risk_heatmap: "text-orange-400 bg-orange-500/10 ring-orange-500/30",
};

function confidenceBadge(score: number): { label: string; cls: string } {
  const pct = Math.round(score * 100);
  if (score >= 0.8)
    return { label: `${pct}%`, cls: "text-emerald-400 bg-emerald-500/10 ring-emerald-500/30" };
  if (score >= 0.6)
    return { label: `${pct}%`, cls: "text-yellow-400 bg-yellow-500/10 ring-yellow-500/30" };
  return { label: `${pct}%`, cls: "text-orange-400 bg-orange-500/10 ring-orange-500/30" };
}

interface DiagramCardProps {
  diagram: Diagram;
  minHeight?: number;
  /** Zero-based index within the section for staggered entrance animation. */
  index?: number;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function FullscreenPortal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Focus management: move focus into the dialog on open (the trigger
  // button was focused a moment ago, but nothing inside this dialog is
  // reachable from there without this) and restore it to whatever
  // triggered the dialog on close, rather than leaving focus stranded or
  // reset to the document body.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => {
      previouslyFocused?.focus?.();
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      // Minimal focus trap: Tab/Shift+Tab wraps within the dialog's own
      // focusable elements instead of escaping to the (hidden-behind-it)
      // page underneath.
      if (event.key === "Tab" && dialogRef.current) {
        const focusable = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-50 flex flex-col bg-slate-950 outline-none"
      role="dialog"
      aria-modal="true"
      tabIndex={-1}
    >
      {children}
    </div>
  );
}

export function DiagramCard({ diagram, minHeight = 320, index = 0 }: DiagramCardProps) {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const badgeClass = DIAGRAM_TYPE_COLORS[diagram.type] ?? "text-slate-400 bg-slate-800 ring-slate-700";
  const confidence = typeof diagram.confidence === "number" ? confidenceBadge(diagram.confidence) : null;
  const evidenceCount = diagram.evidence?.length ?? 0;
  const why = diagram.metadata?.why ? String(diagram.metadata.why) : null;

  function handleHoverEnter() {
    if (cardRef.current) {
      cardRef.current.style.boxShadow =
        "0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(148,163,184,0.08)";
      cardRef.current.style.transform = "translateY(-2px)";
    }
  }
  function handleHoverLeave() {
    if (cardRef.current) {
      cardRef.current.style.boxShadow = "";
      cardRef.current.style.transform = "";
    }
  }

  function renderHeader(fullscreen: boolean) {
    return (
      <div
        className={`flex items-start justify-between border-b border-slate-800 px-4 py-3 ${
          fullscreen ? "" : ""
        }`}
      >
        <div className="min-w-0 flex-1">
          {/* Top row: type badge + title */}
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${badgeClass}`}
            >
              {DIAGRAM_TYPE_LABELS[diagram.type] ?? diagram.type}
            </span>
            {confidence && (
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset ${confidence.cls}`}
                title="Confidence score"
              >
                {confidence.label} confidence
              </span>
            )}
            {evidenceCount > 0 && (
              <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">
                {evidenceCount} source{evidenceCount !== 1 ? "s" : ""}
              </span>
            )}
          </div>

          {/* Title */}
          <h3 className={`mt-1.5 font-semibold text-slate-100 ${fullscreen ? "text-base" : "truncate text-sm"}`}>
            {diagram.title}
          </h3>

          {/* Description / why */}
          {(why ?? diagram.description) && (
            <p
              className={`mt-0.5 text-xs text-slate-500 ${
                fullscreen ? "line-clamp-3" : "truncate"
              }`}
            >
              {why ?? diagram.description}
            </p>
          )}
        </div>

        {/* Action buttons */}
        <div className="ml-2 flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => {/* future export */}}
            className="rounded-lg p-1.5 text-slate-600 transition-colors hover:bg-slate-800 hover:text-slate-400"
            aria-label="Export diagram (coming soon)"
            title="Export (coming soon)"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
            aria-label={isFullscreen ? "Exit fullscreen" : `View ${diagram.title} fullscreen`}
          >
            {isFullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
    );
  }

  if (isFullscreen) {
    return (
      <FullscreenPortal onClose={() => setIsFullscreen(false)}>
        {renderHeader(true)}
        <div className="flex-1 overflow-hidden">
          <div style={{ height: "100%" }} className="w-full">
            <BlueprintRenderer diagram={diagram} />
          </div>
        </div>
      </FullscreenPortal>
    );
  }

  return (
    <div
      ref={cardRef}
      className="flex flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60"
      style={{
        animation: "diagram-card-in 0.45s ease-out backwards",
        animationDelay: `${index * 55}ms`,
        transition: "box-shadow 0.2s ease, transform 0.2s ease",
      }}
      onMouseEnter={handleHoverEnter}
      onMouseLeave={handleHoverLeave}
    >
      {renderHeader(false)}

      <div className="overflow-hidden" style={{ height: minHeight }}>
        <BlueprintRenderer diagram={diagram} />
      </div>

      {Boolean(diagram.metadata?.note) && (
        <p className="border-t border-slate-800 px-4 py-2 text-[10px] text-slate-600">
          {String(diagram.metadata?.note)}
        </p>
      )}
    </div>
  );
}
