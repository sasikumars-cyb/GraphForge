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

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import {
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  ChevronLeft,
  Presentation,
  Rows3,
  AlertCircle,
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

/** Cards that benefit from extra vertical space — graph-based and vertical content. */
const TALL_TYPES = new Set<DiagramType>(["flow", "architecture", "dependency", "er", "risk_heatmap"]);

/** Graph types rendered via dagre + ReactFlow — their ideal height tracks node count,
 * not a flat constant. A 1-2 node diagram in a fixed 460px box is mostly dead space;
 * a 12-node diagram needs more room than that to stay legible. */
const GRAPH_TYPES = new Set<DiagramType>(["flow", "architecture", "dependency", "sequence", "er"]);

function diagramHeight(diagram: Diagram, isTall: boolean): number {
  if (GRAPH_TYPES.has(diagram.type as DiagramType)) {
    const nodeCount = diagram.nodes?.length ?? 0;
    const direction = diagram.layout?.direction ?? "LR";
    // TB/BT layouts stack nodes vertically, so they need height sooner than
    // LR layouts, which spread nodes sideways within the same box.
    const perNode = direction === "TB" || direction === "BT" ? 55 : 30;
    return Math.min(460, Math.max(200, 160 + nodeCount * perNode));
  }
  return isTall ? 460 : 380;
}

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
// Executive summary stats — the numbers a non-technical reviewer (CEO,
// delivery manager) actually scans for before reading a single diagram.
// Derived entirely from data already in the blueprint — no new backend field.
// ---------------------------------------------------------------------------

interface BlueprintStats {
  implementationSteps: number | null;
  risks: number | null;
  affectedComponents: number | null;
  confidencePct: number | null;
}

function computeBlueprintStats(diagrams: Diagram[]): BlueprintStats {
  const planDiagram = diagrams.find(
    (d) => d.metadata?.section === "Implementation Plan" || d.metadata?.section === "Implementation Flow",
  );
  const riskDiagram = diagrams.find((d) => d.type === "risk_heatmap");
  const componentIds = new Set<string>();
  for (const d of diagrams) {
    if (d.type !== "architecture" && d.type !== "dependency") continue;
    for (const n of d.nodes) {
      if (n.type === "component") componentIds.add(n.id);
    }
  }

  const scored = diagrams.filter((d) => typeof d.confidence === "number") as (Diagram & { confidence: number })[];
  const avgConfidence =
    scored.length > 0 ? scored.reduce((sum, d) => sum + d.confidence, 0) / scored.length : null;

  return {
    implementationSteps: planDiagram ? planDiagram.nodes.length : null,
    risks: riskDiagram ? riskDiagram.nodes.length : null,
    affectedComponents: componentIds.size > 0 ? componentIds.size : null,
    confidencePct: avgConfidence != null ? Math.round(avgConfidence * 100) : null,
  };
}

// ---------------------------------------------------------------------------
// Diagram validation
// ---------------------------------------------------------------------------

interface ValidationResult {
  valid: boolean;
  reason: string;
}

/** Nodes the LLM emitted with no real label — these render as blank boxes
 * with edges dangling into empty space, which reads as broken rendering
 * even though the node/edge *counts* pass validation. */
function hasUnlabeledNode(diagram: Diagram): boolean {
  return diagram.nodes.some((n) => !n.label || !n.label.trim());
}

function validateDiagram(diagram: Diagram): ValidationResult {
  const n = diagram.nodes.length;
  const e = diagram.edges.length;
  switch (diagram.type) {
    case "architecture":
      if (n < 3)
        return { valid: false, reason: `Needs at least 3 architecture layers — found ${n}.` };
      if (e === 0) return { valid: false, reason: "No connections between architecture layers." };
      if (hasUnlabeledNode(diagram))
        return { valid: false, reason: "One or more architecture layers were generated without a label." };
      return { valid: true, reason: "" };
    case "flow":
      if (n < 3)
        return { valid: false, reason: `Needs at least 3 data flow stages — found ${n}.` };
      if (e === 0) return { valid: false, reason: "No connections between flow stages." };
      if (hasUnlabeledNode(diagram))
        return { valid: false, reason: "One or more flow stages were generated without a label." };
      return { valid: true, reason: "" };
    case "er":
      if (n < 2)
        return { valid: false, reason: `Needs at least 2 data entities — found ${n}.` };
      if (e === 0) return { valid: false, reason: "No relationships defined between entities." };
      return { valid: true, reason: "" };
    case "dependency":
      if (n < 3)
        return { valid: false, reason: `Needs at least 3 nodes to show meaningful relationships — found ${n}.` };
      if (e === 0) return { valid: false, reason: "No connections between components." };
      if (hasUnlabeledNode(diagram))
        return { valid: false, reason: "One or more components were generated without a label." };
      return { valid: true, reason: "" };
    case "sequence":
      if (n < 2 || e === 0)
        return {
          valid: false,
          reason: "Needs at least 2 participants and at least 1 interaction.",
        };
      return { valid: true, reason: "" };
    case "timeline":
      if (n < 2) return { valid: false, reason: `Needs at least 2 phases — found ${n}.` };
      return { valid: true, reason: "" };
    case "risk_heatmap":
      if (n === 0) return { valid: false, reason: "No risks were identified." };
      return { valid: true, reason: "" };
    default:
      if (n === 0) return { valid: false, reason: "No diagram data available." };
      return { valid: true, reason: "" };
  }
}

const SECTION_EMPTY_MESSAGES: Record<string, { heading: string; body: string }> = {
  Architecture: {
    heading: "Architecture synthesized from requirements only.",
    body: "Not enough distinct layers were identified to generate a meaningful architecture diagram. The implementation plan above describes the intended approach.",
  },
  "Data Flow": {
    heading: "Not enough stages to generate a data flow diagram.",
    body: "At least 3 distinct processing stages are required. The implementation steps describe the intended data flow.",
  },
  "Repository Analysis": {
    heading: "No repositories are currently indexed.",
    body: "Connect GitHub, GitLab, or Bitbucket in Settings → Repositories, then re-run this plan.",
  },
  "Data Model": {
    heading: "Data model could not be generated.",
    body: "Fewer than 2 entities with defined relationships were identified from the requirements.",
  },
};

function DiagramInvalidState({
  diagram,
  reason,
  sectionTitle,
}: {
  diagram: Diagram;
  reason: string;
  sectionTitle: string;
}) {
  const msg = SECTION_EMPTY_MESSAGES[sectionTitle];
  return (
    <div className="flex min-h-[160px] flex-col items-center justify-center gap-2 rounded-xl border border-slate-800 bg-slate-900/40 px-6 py-8 text-center">
      <AlertCircle className="h-6 w-6 text-slate-600" aria-hidden="true" />
      <p className="text-sm font-medium text-slate-400">{msg?.heading ?? diagram.title}</p>
      <p className="max-w-sm text-xs text-slate-500">{msg?.body ?? reason}</p>
    </div>
  );
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
  /** When true, all sections start expanded (e.g. Planning page hero). Default: false (collapsed). */
  defaultExpanded?: boolean;
  /** Real counts from the step result (PlanningResult/DevelopmentPlanResult),
   * when the caller has them. Diagram nodes are an approximation of these —
   * e.g. an architecture diagram's "component"-typed nodes rarely match the
   * actual affected_components list — so a caller-supplied count always
   * wins over the derived one instead of silently showing a different,
   * lower number elsewhere on the same page. */
  affectedComponentsCount?: number;
  implementationStepsCount?: number;
  risksCount?: number;
}

export function BlueprintExplorer({
  blueprint,
  defaultExpanded = false,
  affectedComponentsCount,
  implementationStepsCount,
  risksCount,
}: BlueprintExplorerProps) {
  // `groupIntoSections` is a pure function of `blueprint.diagrams`, but was
  // called unmemoized directly in the render body — a fresh
  // BlueprintSection[] (and fresh `.diagrams` sub-arrays) on every render,
  // including ones triggered by unrelated state in this same component
  // (`collapsed`, `presentationMode`, `slideIndex`). Memoizing means every
  // consumer below that keys or diffs off `sections` — the DiagramCard
  // list, the IntersectionObserver setup, the localStorage-sync effect —
  // sees the same array identity across an unrelated re-render instead of
  // a same-content-different-identity replacement.
  const sections = useMemo(() => groupIntoSections(blueprint.diagrams), [blueprint.diagrams]);
  const storageKey = `graphforge.blueprint.expanded.${blueprint.agent_id}.${blueprint.stage}`;

  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    if (defaultExpanded) return new Set(); // all open
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
          {slide.diagrams.map((diagram, idx) => {
            const v = validateDiagram(diagram);
            if (!v.valid) {
              return (
                <DiagramInvalidState
                  key={diagram.id}
                  diagram={diagram}
                  reason={v.reason}
                  sectionTitle={slide.title}
                />
              );
            }
            return (
              <DiagramCard
                key={diagram.id}
                diagram={diagram}
                minHeight={diagramHeight(diagram, true)}
                index={idx}
              />
            );
          })}
        </div>
      </div>
    );
  }

  const stats = computeBlueprintStats(blueprint.diagrams);
  const implementationSteps = implementationStepsCount ?? stats.implementationSteps;
  const affectedComponents = affectedComponentsCount ?? stats.affectedComponents;
  const risks = risksCount ?? stats.risks;
  // Explicitly typed so every element (before filtering) shares the same
  // `value: string | number` shape — without this, TS infers each object
  // literal's `value` at its own narrow type (number here, the template
  // string there), and a type predicate can only narrow that inferred
  // union, never widen it back to `string | number`.
  const statItems: (false | { label: string; value: string | number })[] = [
    implementationSteps != null && { label: "Implementation Steps", value: implementationSteps },
    affectedComponents != null && { label: "Affected Components", value: affectedComponents },
    risks != null && { label: "Risks Identified", value: risks },
    stats.confidencePct != null && { label: "Confidence", value: `${stats.confidencePct}%` },
  ];
  const visibleStatItems = statItems.filter(
    (x): x is { label: string; value: string | number } => Boolean(x),
  );

  return (
    <div className="flex flex-col gap-0">
      {/* ── Executive summary strip — the glanceable numbers, before any diagram ── */}
      {visibleStatItems.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {visibleStatItems.map((s) => (
            <div
              key={s.label}
              className="flex items-baseline gap-1.5 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-1.5"
            >
              <span className="text-sm font-semibold text-slate-100">{s.value}</span>
              <span className="text-[10.5px] font-medium text-slate-500">{s.label}</span>
            </div>
          ))}
        </div>
      )}

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
                <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))" }}>
                  {section.diagrams.map((diagram, idx) => {
                    const v = validateDiagram(diagram);
                    if (!v.valid) {
                      return (
                        <DiagramInvalidState
                          key={diagram.id}
                          diagram={diagram}
                          reason={v.reason}
                          sectionTitle={section.title}
                        />
                      );
                    }
                    return (
                      <DiagramCard
                        key={diagram.id}
                        diagram={diagram}
                        minHeight={diagramHeight(diagram, TALL_TYPES.has(diagram.type as DiagramType))}
                        index={idx}
                      />
                    );
                  })}
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
