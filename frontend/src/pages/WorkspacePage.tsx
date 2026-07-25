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
      bg: "bg-sky-500/10",
      ring: "ring-sky-500/30",
      text: "text-sky-400",
      hover: "hover:border-sky-500/40",
    },
    violet: {
      bg: "bg-violet-500/10",
      ring: "ring-violet-500/30",
      text: "text-violet-400",
      hover: "hover:border-violet-500/40",
    },
    amber: {
      bg: "bg-amber-500/10",
      ring: "ring-amber-500/30",
      text: "text-amber-400",
      hover: "hover:border-amber-500/40",
    },
    emerald: {
      bg: "bg-emerald-500/10",
      ring: "ring-emerald-500/30",
      text: "text-emerald-400",
      hover: "hover:border-emerald-500/40",
    },
    rose: {
      bg: "bg-rose-500/10",
      ring: "ring-rose-500/30",
      text: "text-rose-400",
      hover: "hover:border-rose-500/40",
    },
    teal: {
      bg: "bg-teal-500/10",
      ring: "ring-teal-500/30",
      text: "text-teal-400",
      hover: "hover:border-teal-500/40",
    },
    orange: {
      bg: "bg-orange-500/10",
      ring: "ring-orange-500/30",
      text: "text-orange-400",
      hover: "hover:border-orange-500/40",
    },
    indigo: {
      bg: "bg-indigo-500/10",
      ring: "ring-indigo-500/30",
      text: "text-indigo-400",
      hover: "hover:border-indigo-500/40",
    },
  };

  const colors = colorMap[capability.color] ?? colorMap.sky;

  if (!capability.available) {
    return (
      <div className="relative flex flex-col gap-3 rounded-xl border border-slate-800/60 bg-slate-900/30 p-5 opacity-60">
        <div className="flex items-center gap-3">
          <div className={`rounded-lg ${colors.bg} p-2.5 ring-1 ring-inset ${colors.ring}`}>
            <capability.icon className={`h-5 w-5 ${colors.text}`} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-slate-300">{capability.name}</h3>
          </div>
        </div>
        <p className="text-xs leading-relaxed text-slate-500">{capability.description}</p>
        <span className="mt-auto inline-flex w-fit items-center rounded-full bg-slate-800/80 px-2.5 py-1 text-[10px] font-medium tracking-wide text-slate-500 uppercase">
          Coming Soon
        </span>
      </div>
    );
  }

  return (
    <Link
      to={`/workspace/${capability.slug}`}
      className={`group relative flex flex-col gap-3 rounded-xl border border-slate-800 bg-slate-900/60 p-5 shadow-sm shadow-black/20 transition-colors ${colors.hover} hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500`}
    >
      <div className="flex items-center gap-3">
        <div className={`rounded-lg ${colors.bg} p-2.5 ring-1 ring-inset ${colors.ring}`}>
          <capability.icon className={`h-5 w-5 ${colors.text}`} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-100 group-hover:text-white">
            {capability.name}
          </h3>
        </div>
      </div>
      <p className="text-xs leading-relaxed text-slate-400">{capability.description}</p>
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
    const matchesSearch =
      !search ||
      cap.name.toLowerCase().includes(search.toLowerCase()) ||
      cap.description.toLowerCase().includes(search.toLowerCase());
    const matchesCategory = activeCategory === "all" || cap.category === activeCategory;
    return matchesSearch && matchesCategory;
  });

  const available = filtered.filter((c) => c.available);
  const comingSoon = filtered.filter((c) => !c.available);

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-brand-500/10 p-2 ring-1 ring-inset ring-brand-500/30">
            <Sparkles className="h-5 w-5 text-brand-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">AI Workspace</h2>
            <p className="text-sm text-slate-400">
              Discover and run AI-powered engineering capabilities.
            </p>
          </div>
        </div>

        {/* Search */}
        <div className="relative w-full sm:w-64">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
            aria-hidden="true"
          />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search capabilities..."
            className="w-full rounded-lg border border-slate-700 bg-slate-900 py-2 pl-9 pr-3 text-sm text-slate-100 placeholder-slate-500 outline-none transition-colors focus:border-brand-500/60 focus:ring-1 focus:ring-brand-500/30"
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
              ? "bg-brand-500/20 text-brand-300 ring-1 ring-inset ring-brand-500/40"
              : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
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
                ? "bg-brand-500/20 text-brand-300 ring-1 ring-inset ring-brand-500/40"
                : "text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            }`}
          >
            {CATEGORY_LABELS[cat]}
          </button>
        ))}
      </div>

      {/* Available capabilities */}
      {available.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {available.map((cap) => (
            <CapabilityCard key={cap.slug} capability={cap} />
          ))}
        </div>
      )}

      {/* Coming soon */}
      {comingSoon.length > 0 && (
        <div>
          <h3 className="mb-3 text-xs font-semibold tracking-wide text-slate-600 uppercase">
            Coming Soon
          </h3>
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
          <Search className="mb-3 h-8 w-8 text-slate-600" aria-hidden="true" />
          <p className="text-sm text-slate-400">No capabilities match your search.</p>
        </div>
      )}
    </div>
  );
}
