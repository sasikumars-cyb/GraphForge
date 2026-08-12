import { useState } from "react";
import { Link } from "react-router-dom";
import { Search, Sparkles } from "lucide-react";
import {
  WORKSPACE_CAPABILITIES,
  CATEGORY_LABELS,
  type CapabilityCategory,
  type WorkspaceCapability,
} from "../config/workspace-capabilities";

function CapabilityCard({ capability }: { capability: WorkspaceCapability }) {
  const colorMap: Record<string, { bg: string; ring: string; text: string; hover: string }> = {
    sky: {
      bg: "bg-info-bg",
      ring: "ring-info-line/30",
      text: "text-info-fg",
      hover: "hover:border-info-line/40",
    },
    violet: {
      bg: "bg-cat-7-bg",
      ring: "ring-cat-7-line/30",
      text: "text-cat-7-fg",
      hover: "hover:border-cat-7-line/40",
    },
    amber: {
      bg: "bg-warning-bg",
      ring: "ring-warning-line/30",
      text: "text-warning-fg",
      hover: "hover:border-warning-line/40",
    },
    emerald: {
      bg: "bg-success-bg",
      ring: "ring-success-line/30",
      text: "text-success-fg",
      hover: "hover:border-success-line/40",
    },
    rose: {
      bg: "bg-danger-bg",
      ring: "ring-danger-line/30",
      text: "text-danger-fg",
      hover: "hover:border-danger-line/40",
    },
    teal: {
      bg: "bg-cat-5-bg",
      ring: "ring-cat-5-line/30",
      text: "text-cat-5-fg",
      hover: "hover:border-cat-5-line/40",
    },
    orange: {
      bg: "bg-cat-6-bg",
      ring: "ring-cat-6-line/30",
      text: "text-cat-6-fg",
      hover: "hover:border-cat-6-line/40",
    },
    indigo: {
      bg: "bg-cat-7-bg",
      ring: "ring-cat-7-line/30",
      text: "text-cat-7-fg",
      hover: "hover:border-cat-7-line/40",
    },
  };

  const colors = colorMap[capability.color] ?? colorMap.sky;

  if (!capability.available) {
    return (
      <div className="relative flex flex-col gap-3 rounded-xl border border-line-muted bg-surface p-5 opacity-60">
        <div className="flex items-center gap-3">
          <div className={`rounded-lg ${colors.bg} p-2.5 ring-1 ring-inset ${colors.ring}`}>
            <capability.icon className={`h-5 w-5 ${colors.text}`} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-fg-secondary">{capability.name}</h3>
          </div>
        </div>
        <p className="text-xs leading-relaxed text-fg-muted">{capability.description}</p>
        <span className="mt-auto inline-flex w-fit items-center rounded-full bg-surface-raised px-2.5 py-1 text-[10px] font-medium tracking-wide text-fg-muted uppercase">
          Coming Soon
        </span>
      </div>
    );
  }

  return (
    <Link
      to={`/workspace/${capability.slug}`}
      className={`group relative flex flex-col gap-3 rounded-xl border border-line-muted bg-surface p-5 shadow-sm transition-colors ${colors.hover} hover:bg-surface `}
    >
      <div className="flex items-center gap-3">
        <div className={`rounded-lg ${colors.bg} p-2.5 ring-1 ring-inset ${colors.ring}`}>
          <capability.icon className={`h-5 w-5 ${colors.text}`} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-fg group-hover:text-fg">{capability.name}</h3>
        </div>
      </div>
      <p className="text-xs leading-relaxed text-fg-muted">{capability.description}</p>
      <span
        className={`mt-auto inline-flex w-fit items-center rounded-full ${colors.bg} px-2.5 py-1 text-[10px] font-medium tracking-wide ${colors.text} uppercase`}
      >
        {CATEGORY_LABELS[capability.category]}
      </span>
    </Link>
  );
}

const ALL_CATEGORIES: CapabilityCategory[] = ["plan", "build", "test", "review", "explore"];

export function WorkspacePage() {
  const [search, setSearch] = useState("");
  const [activeCategory, setActiveCategory] = useState<CapabilityCategory | "all">("all");

  const filtered = WORKSPACE_CAPABILITIES.filter((cap) => {
    if (cap.hidden) return false;
    const matchesSearch =
      !search ||
      cap.name.toLowerCase().includes(search.toLowerCase()) ||
      cap.description.toLowerCase().includes(search.toLowerCase());
    const matchesCategory = activeCategory === "all" || cap.category === activeCategory;
    return matchesSearch && matchesCategory;
  });

  // Alphabetical within each group — the catalog has grown past the size
  // where source-file insertion order reads as intentional; a stable,
  // predictable ordering the reader doesn't have to reverse-engineer
  // beats one that just reflects the order capabilities were added.
  const byName = (a: WorkspaceCapability, b: WorkspaceCapability) => a.name.localeCompare(b.name);
  const available = filtered.filter((c) => c.available).sort(byName);
  const comingSoon = filtered.filter((c) => !c.available).sort(byName);

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-accent-bg p-2 ring-1 ring-inset ring-accent-line/30">
            <Sparkles className="h-5 w-5 text-accent-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">AI Workspace</h1>
            <p className="text-sm text-fg-muted">
              Discover and run AI-powered engineering capabilities.
            </p>
          </div>
        </div>

        {/* Search */}
        <div className="relative w-full sm:w-64">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted"
            aria-hidden="true"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search capabilities..."
            className="w-full rounded-lg border border-line bg-surface py-2 pl-9 pr-3 text-sm text-fg placeholder-fg-subtle transition-colors focus:border-accent-line/60 "
          />
        </div>
      </div>

      {/* Category filters */}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setActiveCategory("all")}
          className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
            activeCategory === "all"
              ? "bg-accent-bg text-accent-fg ring-1 ring-inset ring-accent-line/40"
              : "text-fg-muted hover:bg-surface-raised hover:text-fg-secondary"
          }`}
        >
          All
        </button>
        {ALL_CATEGORIES.map((cat) => (
          <button
            key={cat}
            type="button"
            onClick={() => setActiveCategory(cat)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
              activeCategory === cat
                ? "bg-accent-bg text-accent-fg ring-1 ring-inset ring-accent-line/40"
                : "text-fg-muted hover:bg-surface-raised hover:text-fg-secondary"
            }`}
          >
            {CATEGORY_LABELS[cat]}
          </button>
        ))}
      </div>

      {/* Available capabilities */}
      {available.length > 0 && (
        <div>
          {/* The "Coming Soon" group below has a visible heading; this group
              had none, so its capability names jumped straight from the page
              <h1> to <h3>. Named for assistive tech, unstyled for sight. */}
          <h2 className="sr-only">Available capabilities</h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {available.map((cap) => (
              <CapabilityCard key={cap.slug} capability={cap} />
            ))}
          </div>
        </div>
      )}

      {/* Coming soon */}
      {comingSoon.length > 0 && (
        <div>
          <h2 className="mb-3 text-xs font-semibold tracking-wide text-fg-subtle uppercase">
            Coming Soon
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {comingSoon.map((cap) => (
              <CapabilityCard key={cap.slug} capability={cap} />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {available.length === 0 && comingSoon.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Search className="mb-3 h-8 w-8 text-fg-subtle" aria-hidden="true" />
          <p className="text-sm text-fg-muted">No capabilities match your search.</p>
        </div>
      )}
    </div>
  );
}
