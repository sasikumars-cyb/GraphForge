import { useLocation } from "react-router-dom";
import { Menu, Search } from "lucide-react";
import { NAV_ITEMS } from "./nav-items";
import { WORKSPACE_CAPABILITIES } from "../../config/workspace-capabilities";

interface TopbarProps {
  onMenuClick: () => void;
  onOpenPalette: () => void;
}

export function Topbar({ onMenuClick, onOpenPalette }: TopbarProps) {
  const location = useLocation();

  // Resolve page title: first check workspace capabilities (nested routes),
  // then fall back to top-level nav items.
  let pageTitle: string | undefined;

  if (location.pathname.startsWith("/workspace/")) {
    const slug = location.pathname.replace("/workspace/", "").split("/")[0];
    const cap = WORKSPACE_CAPABILITIES.find((c) => c.slug === slug);
    pageTitle = cap?.name;
  }

  if (!pageTitle) {
    const navItem = NAV_ITEMS.find((item) =>
      item.path === "/" ? location.pathname === "/" : location.pathname.startsWith(item.path),
    );
    pageTitle = navItem?.label;
  }

  return (
    <header className="sticky top-0 z-20 flex items-center justify-between border-b border-line-muted bg-canvas/80 px-4 py-3 backdrop-blur md:px-8">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onMenuClick}
          className="focus-ring rounded-md p-1.5 text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg md:hidden"
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" />
        </button>
        {/* Not a heading: this mirrors the current sidebar selection as
            chrome, and every page below renders its own <h1> with the same
            text. Two competing <h1>s (or an <h1> that repeats the page
            title) is worse for heading navigation than one. */}
        <p className="font-display text-base font-semibold tracking-tight text-fg">
          {pageTitle ?? "GraphForge"}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onOpenPalette}
          className="focus-ring hidden items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover hover:text-fg-secondary sm:flex"
          aria-label="Open command palette"
        >
          <Search className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Jump to…</span>
          <kbd className="rounded border border-line px-1 text-[10px]">⌘K</kbd>
        </button>
      </div>
    </header>
  );
}
