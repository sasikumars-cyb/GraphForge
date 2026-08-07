import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, CornerDownLeft, type LucideIcon } from "lucide-react";
import { NAV_SECTIONS } from "./nav-items";

interface PaletteItem {
  label: string;
  path: string;
  section: string;
  icon: LucideIcon;
}

/** Every navigable destination plus a couple of direct actions that don't
 * have their own sidebar entry, flattened once at module scope — this
 * never changes at runtime, so there's no reason to rebuild it per render
 * or per component instance. */
const ALL_ITEMS: PaletteItem[] = NAV_SECTIONS.flatMap((section) =>
  section.items.map((item) => ({
    label: item.label,
    path: item.path,
    section: section.section ?? "Go to",
    icon: item.icon,
  })),
);

function filterItems(query: string): PaletteItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return ALL_ITEMS;
  return ALL_ITEMS.filter((item) => item.label.toLowerCase().includes(q));
}

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * ⌘K / Ctrl+K global navigation — a 28-route product with zero keyboard
 * shortcuts meant every page change was a mouse trip to the sidebar,
 * including for capabilities filed under the Workspace catalog rather than
 * the sidebar itself. Navigation-only for now: jumping to a graph node by
 * name is a real, larger want (see the Architecture page audit) but needs
 * a backend search endpoint this pass deliberately didn't add — this
 * covers the part achievable with what already exists (the same NAV_ITEMS
 * the sidebar renders from), not a placeholder for the rest.
 *
 * Controlled, not self-owned: Topbar renders a visible "⌘K" affordance for
 * anyone who wouldn't otherwise discover the shortcut, so open state lives
 * in the shared parent (AppLayout) rather than being trapped inside this
 * component with no way for a sibling to trigger it.
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const results = useMemo(() => filterItems(query), [query]);

  // Global toggle, active on every authenticated page (mounted once in
  // AppLayout) — Cmd on macOS, Ctrl elsewhere, both checked since this
  // runs on whatever OS the browser reports without a build-time branch.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onOpenChange(!open);
        return;
      }
      if (e.key === "Escape") onOpenChange(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Focus after the dialog paints, not synchronously — the input
      // doesn't exist yet on the render that flips `open` true.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  function go(path: string) {
    navigate(path);
    onOpenChange(false);
  }

  function onListKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const item = results[activeIndex];
      if (item) go(item.path);
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      <div className="absolute inset-0 bg-canvas/80" onClick={() => onOpenChange(false)} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="relative flex max-h-[60vh] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-lg"
      >
        <div className="flex items-center gap-2 border-b border-line-muted px-4 py-3">
          <Search className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onListKeyDown}
            placeholder="Jump to a page…"
            className="w-full bg-transparent text-sm text-fg placeholder-fg-subtle focus:outline-none"
            aria-label="Search pages"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={
              results[activeIndex] ? `command-palette-item-${activeIndex}` : undefined
            }
          />
          <kbd className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-subtle">
            Esc
          </kbd>
        </div>
        <ul id="command-palette-list" role="listbox" className="flex-1 overflow-y-auto p-1.5">
          {results.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-fg-muted">No pages match “{query}”.</li>
          ) : (
            results.map((item, i) => {
              const Icon = item.icon;
              const isActive = i === activeIndex;
              return (
                <li
                  key={item.path}
                  id={`command-palette-item-${i}`}
                  role="option"
                  aria-selected={isActive}
                >
                  <button
                    type="button"
                    onClick={() => go(item.path)}
                    onMouseEnter={() => setActiveIndex(i)}
                    className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                      isActive ? "bg-accent-bg text-accent-fg" : "text-fg-secondary hover:bg-surface-hover"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    <span className="shrink-0 text-xs text-fg-subtle">{item.section}</span>
                    {isActive && <CornerDownLeft className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
                  </button>
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}
