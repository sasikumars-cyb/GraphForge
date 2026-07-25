/**
 * BlueprintExplorer — section-organised Visual Blueprint with sticky navigation.
 *
 * Features:
 * - Sections grouped by diagram.metadata.section (Architecture, Data Flow, etc.)
 * - Sticky horizontal pill nav for quick jump-to-section
 * - Active section tracking via IntersectionObserver
 * - Collapsible sections (toggle per section, all open by default)
 * - Staggered entrance animation on diagram cards
 * - Works for Planning and Development blueprints without changes
 */

import { useState, useEffect, useRef, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  ChevronLeft,
  Presentation,
  Rows3,
} from "lucide-react";
import type { BlueprintArtifact, Diagram, DiagramType } from "../../types/blueprint";
import { DiagramCard } from "./DiagramCard";

// ---------------------------------------------------------------------------
// Section configuration
// ---------------------------------------------------------------------------

const SECTION_ORDER = [
  // Planning — ordered as a solution design review reads:
  // what we're building, how it runs, what data it owns, what we reuse,
  // how we build it, what could go wrong.
  "Architecture",
  "Data Flow",
  "Data Model",
  "Repository Analysis",
  "Implementation Plan",
  "Risks",
  // Development
  "Implementation Overview",
  "File Modifications",
  "Component Dependencies",
  "Implementation Flow",
  "Code Impact",
  "Test Strategy",
  "Implementation Risks",
  // Fallback
  "Diagrams",
];

const SECTION_DESCRIPTIONS: Record<string, string> = {
  "Architecture":            "High-level solution layers from data source to consumption",
  "Data Flow":               "Operational data movement end-to-end through the system",
  "Repository Analysis":     "Existing repositories analysed and rated for this solution",
  "Data Model":              "Business domain entities and their relationships",
  "Implementation Plan":     "Engineering phases from foundation to production deployment",
  "Risks":                   "Architectural risks assessed by likelihood, impact, and mitigation",
  "Implementation Overview": "Repositories and architectural layers requiring changes",
  "File Modifications":      "Changed files and classes organised by repository",
  "Component Dependencies":  "Cross-component and cross-repository dependency graph",
  "Implementation Flow":     "Engineering phases from planning through deployment",
  "Code Impact":             "Impact across API, service, data, and integration layers",
  "Test Strategy":           "Test coverage approach across unit, integration, and E2E layers",
  "Implementation Risks":    "Technical debt, breaking changes, and migration risks",
};

/** Cards that benefit from extra vertical space — predominantly vertical content. */
const TALL_TYPES = new Set<DiagramType>(["flow", "risk_heatmap"]);

interface BlueprintSection {
  id: string;
  title: string;
  description: string;
  diagrams: Diagram[];
}

function groupIntoSections(diagrams: Diagram[]): BlueprintSection[] {
  const bySection = new Map<string, Diagram[]>();
  for (const d of diagrams) {
    const sectionName = String(d.metadata?.section ?? "Diagrams");
    if (!bySection.has(sectionName)) bySection.set(sectionName, []);
    bySection.get(sectionName)!.push(d);
  }

  const result: BlueprintSection[] = [];
  const seen = new Set<string>();

  for (const name of SECTION_ORDER) {
    if (bySection.has(name)) {
      result.push({
        id: `bp-section-${name.toLowerCase().replace(/\s+/g, "-")}`,
        title: name,
        description: SECTION_DESCRIPTIONS[name] ?? "",
        diagrams: bySection.get(name)!,
      });
      seen.add(name);
    }
  }

  // Any section not in SECTION_ORDER goes at the end
  for (const [name, diags] of bySection) {
    if (!seen.has(name)) {
      result.push({
        id: `bp-section-${name.toLowerCase().replace(/\s+/g, "-")}`,
        title: name,
        description: SECTION_DESCRIPTIONS[name] ?? "",
        diagrams: diags,
      });
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyBlueprint() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <LayoutGrid className="h-8 w-8 text-slate-600" aria-hidden="true" />
      <p className="text-sm text-slate-500">No visual diagrams were generated for this stage.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface BlueprintExplorerProps {
  blueprint: BlueprintArtifact;
}

export function BlueprintExplorer({ blueprint }: BlueprintExplorerProps) {
  const sections = groupIntoSections(blueprint.diagrams);
  const storageKey = `graphforge.blueprint.expanded.${blueprint.agent_id}.${blueprint.stage}`;

  // Diagram sections start collapsed — a full blueprint is 6-7 graph-heavy
  // cards and expanding them all produces an unreadable page. The executive
  // summary above this component is already visible, so the reader gets the
  // overview first and opens the diagrams they actually want.
  // Restores the previous session's choices when available.
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    const allIds = sections.map((s) => s.id);
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const expandedIds: string[] = JSON.parse(saved);
        return new Set(allIds.filter((id) => !expandedIds.includes(id)));
      }
    } catch {
      // Corrupt or unavailable storage — fall through to the default.
    }
    return new Set(allIds);
  });

  const [presentationMode, setPresentationMode] = useState(false);
  const [slideIndex, setSlideIndex] = useState(0);
  const [activeSection, setActiveSection] = useState<string | null>(
    sections[0]?.id ?? null
  );

  // Persist which sections are open, not which are closed, so newly added
  // sections in a future blueprint default to collapsed rather than open.
  useEffect(() => {
    try {
      const expandedIds = sections.map((s) => s.id).filter((id) => !collapsed.has(id));
      localStorage.setItem(storageKey, JSON.stringify(expandedIds));
    } catch {
      // Storage unavailable (private mode, quota) — expansion state is a
      // convenience, never worth breaking the render over.
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collapsed, storageKey]);

  const sectionElems = useRef<Map<string, HTMLElement>>(new Map());

  const setSectionRef = useCallback((id: string, el: HTMLElement | null) => {
    if (el) sectionElems.current.set(id, el);
    else sectionElems.current.delete(id);
  }, []);

  // Track which section is in view
  useEffect(() => {
    if (sections.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        // Pick the topmost intersecting section
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          setActiveSection(visible[0].target.id);
        }
      },
      { rootMargin: "-10% 0px -60% 0px", threshold: 0 }
    );

    for (const el of sectionElems.current.values()) {
      observer.observe(el);
    }
    return () => observer.disconnect();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sections.length]);

  function scrollToSection(id: string) {
    // Sections default to collapsed, so jumping to one has to open it —
    // otherwise the pill scrolls to a header with nothing under it.
    setCollapsed((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    const el = sectionElems.current.get(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      setActiveSection(id);
    }
  }

  function toggleSection(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function expandAll() {
    setCollapsed(new Set());
  }

  function collapseAll() {
    setCollapsed(new Set(sections.map((s) => s.id)));
  }

  function enterPresentation() {
    // One diagram at a time, so presentation mode always opens the slide
    // it lands on regardless of the reader's saved collapse state.
    const startIndex = Math.max(
      0,
      sections.findIndex((s) => s.id === activeSection)
    );
    setSlideIndex(startIndex);
    setPresentationMode(true);
  }

  if (blueprint.diagrams.length === 0) return <EmptyBlueprint />;

  const totalDiagrams = blueprint.diagrams.length;

  // ── Presentation mode: one section at a time, for design reviews ──
  if (presentationMode) {
    const slide = sections[Math.min(slideIndex, sections.length - 1)];
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3 border-b border-slate-800 pb-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-slate-100">{slide.title}</h2>
            {slide.description && (
              <p className="mt-0.5 truncate text-xs text-slate-500">{slide.description}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="text-[11px] tabular-nums text-slate-500">
              {slideIndex + 1} / {sections.length}
            </span>
            <button
              type="button"
              onClick={() => setSlideIndex((i) => Math.max(0, i - 1))}
              disabled={slideIndex === 0}
              className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-30 disabled:hover:bg-transparent"
              aria-label="Previous section"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setSlideIndex((i) => Math.min(sections.length - 1, i + 1))}
              disabled={slideIndex >= sections.length - 1}
              className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200 disabled:opacity-30 disabled:hover:bg-transparent"
              aria-label="Next section"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setPresentationMode(false)}
              className="ml-1 flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
            >
              <Rows3 className="h-3 w-3" aria-hidden="true" />
              Exit
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4">
          {slide.diagrams.map((diagram, idx) => (
            <DiagramCard
              key={diagram.id}
              diagram={diagram}
              minHeight={480}
              index={idx}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0">
      {/* ── View controls ── */}
      {sections.length > 1 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={expandAll}
            className="rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-400 ring-1 ring-inset ring-slate-800 transition-colors hover:bg-slate-800 hover:text-slate-200"
          >
            Expand all
          </button>
          <button
            type="button"
            onClick={collapseAll}
            className="rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-400 ring-1 ring-inset ring-slate-800 transition-colors hover:bg-slate-800 hover:text-slate-200"
          >
            Collapse all
          </button>
          <button
            type="button"
            onClick={enterPresentation}
            className="flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium text-slate-400 ring-1 ring-inset ring-slate-800 transition-colors hover:bg-slate-800 hover:text-slate-200"
          >
            <Presentation className="h-3 w-3" aria-hidden="true" />
            Presentation mode
          </button>
        </div>
      )}

      {/* ── Sticky section navigation ── */}
      {sections.length > 1 && (
        <div className="sticky top-0 z-20 -mx-4 mb-2 border-b border-slate-800/70 bg-slate-950/95 px-4 py-2 backdrop-blur-sm">
          <div className="flex items-center gap-1.5 overflow-x-auto pb-0.5">
            {sections.map((section) => {
              const isActive = activeSection === section.id;
              return (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => scrollToSection(section.id)}
                  className={`flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium ring-1 ring-inset transition-all duration-200 ${
                    isActive
                      ? "bg-brand-500/20 text-brand-200 ring-brand-500/40 shadow-[0_0_8px_rgba(99,102,241,0.15)]"
                      : "text-slate-500 ring-slate-800 hover:bg-slate-800/60 hover:text-slate-300"
                  }`}
                  aria-current={isActive ? "true" : undefined}
                >
                  {section.title}
                  <span
                    className={`rounded-full px-1 py-0.5 text-[9px] font-semibold ${
                      isActive ? "bg-brand-500/30 text-brand-300" : "bg-slate-800 text-slate-500"
                    }`}
                  >
                    {section.diagrams.length}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Sections ── */}
      <div className="flex flex-col gap-8">
        {sections.map((section) => {
          const isCollapsed = collapsed.has(section.id);
          return (
            <section
              key={section.id}
              id={section.id}
              ref={(el) => setSectionRef(section.id, el)}
              aria-label={section.title}
            >
              {/* Section header */}
              <div className="mb-4 flex items-start gap-3 border-b border-slate-800/60 pb-3">
                <button
                  type="button"
                  onClick={() => toggleSection(section.id)}
                  className="mt-0.5 shrink-0 rounded p-0.5 text-slate-500 transition-colors hover:bg-slate-800 hover:text-slate-300"
                  aria-label={isCollapsed ? `Expand ${section.title}` : `Collapse ${section.title}`}
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="h-4 w-4" aria-hidden="true" />
                  )}
                </button>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold tracking-tight text-slate-100">
                      {section.title}
                    </h2>
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-medium text-slate-400">
                      {section.diagrams.length}
                    </span>
                  </div>
                  {section.description && (
                    <p className="mt-0.5 text-xs text-slate-500">{section.description}</p>
                  )}
                </div>
              </div>

              {/* Section diagrams.
                  Unmounted rather than hidden while collapsed: ReactFlow
                  measures its container on mount, so a diagram mounted inside
                  a `display: none` section computes a zero-size viewport and
                  fitView silently no-ops, leaving the graph unfitted when the
                  section is later opened. Mounting on expand also means
                  closed sections cost nothing. */}
              {!isCollapsed && (
                <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  {section.diagrams.map((diagram, idx) => (
                    <DiagramCard
                      key={diagram.id}
                      diagram={diagram}
                      minHeight={TALL_TYPES.has(diagram.type as DiagramType) ? 400 : 320}
                      index={idx}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {/* ── Footer ── */}
      <p className="mt-6 text-[10px] text-slate-700">
        Blueprint v{blueprint.version} · {blueprint.agent_id} · {totalDiagrams} diagram
        {totalDiagrams !== 1 ? "s" : ""} across {sections.length} section
        {sections.length !== 1 ? "s" : ""} · Click any node to highlight connections ·
        Click ⤢ to go fullscreen
      </p>
    </div>
  );
}
