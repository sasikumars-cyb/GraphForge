import {
  Server,
  GitMerge,
  FolderGit2,
  Network,
  FileBarChart,
  BarChart3,
  Settings,
  Sparkles,
  History,
  CheckCircle2,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  path: string;
  icon: LucideIcon;
}

export interface NavSection {
  /** null renders no header — used for Dashboard, which sits above every
   * grouped section rather than belonging to one. */
  section: string | null;
  items: NavItem[];
}

/**
 * Single source of truth for sidebar links and the topbar's page title.
 *
 * Navigation is organised around user journeys — not implementation concepts.
 *
 * • Build — create and execute AI-powered engineering work
 * • Monitor — observe execution history and status
 * • Knowledge — understand the codebase
 * • Administration — configure GraphForge
 *
 * Individual AI capabilities live inside the AI Workspace catalog (/workspace)
 * rather than cluttering the sidebar.  This scales to 30+ capabilities without
 * sidebar changes.
 */
export const NAV_SECTIONS: NavSection[] = [
  { section: null, items: [{ label: "Control Center", path: "/", icon: Server }] },
  {
    section: "Build",
    items: [
      { label: "AI Workspace", path: "/workspace", icon: Sparkles },
      { label: "New Workflow", path: "/workflows/new", icon: GitMerge },
      { label: "Approved Queue", path: "/workflows/approved", icon: CheckCircle2 },
    ],
  },
  {
    section: "Monitor",
    items: [
      { label: "Runs", path: "/runs", icon: History },
      { label: "Metrics", path: "/metrics", icon: BarChart3 },
    ],
  },
  {
    section: "Knowledge",
    items: [
      { label: "Repositories", path: "/repositories", icon: FolderGit2 },
      { label: "Architecture", path: "/architecture", icon: Network },
    ],
  },
  {
    section: "Administration",
    items: [
      { label: "Reports", path: "/reports", icon: FileBarChart },
      { label: "Settings", path: "/settings", icon: Settings },
    ],
  },
];

/** Flat view of NAV_SECTIONS, derived once — Topbar's page-title lookup
 * doesn't care about grouping, so it keeps using this exactly as before. */
export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);
