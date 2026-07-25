import { useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { NAV_ITEMS } from "./nav-items";
import { WORKSPACE_CAPABILITIES } from "../../config/workspace-capabilities";
import { StatusBadge } from "../StatusBadge";

interface TopbarProps {
  onMenuClick: () => void;
}

export function Topbar({ onMenuClick }: TopbarProps) {
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
    <header className="flex items-center justify-between border-b border-slate-800 bg-slate-950/80 px-4 py-3 backdrop-blur md:px-8">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onMenuClick}
          className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100 md:hidden"
          aria-label="Open navigation"
        >
          <Menu className="h-5 w-5" />
        </button>
        <h1 className="font-display text-base font-semibold tracking-tight text-slate-100">
          {pageTitle ?? "GraphForge"}
        </h1>
      </div>

      <StatusBadge label="Sample data" tone="info" />
    </header>
  );
}
