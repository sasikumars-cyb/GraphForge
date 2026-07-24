import {
  LayoutDashboard,
  GitPullRequest,
  GitMerge,
  FolderGit2,
  Network,
  FileBarChart,
  Settings,
  Lightbulb,
  Code2,
  FlaskConical,
  Search,
  History,
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

/** Single source of truth for sidebar links and the topbar's page title.
 *
 * Grouped into the four categories a first-time user needs to tell apart:
 * Products (single-agent tools, usable without a full workflow), Workflows
 * (start a guided multi-stage run), History (every past/current run,
 * standalone or workflow), and Administration (everything else). Planning/
 * Development/Testing/Review were previously a flat, unlabeled sibling list
 * right next to "Workflows" — reading as shortcuts into a workflow's own
 * stages, which they are not (they carry no workflow_id at all). Grouping
 * them under "Products" fixes that misread without removing the feature or
 * changing a single route. */
export const NAV_SECTIONS: NavSection[] = [
  { section: null, items: [{ label: "Dashboard", path: "/", icon: LayoutDashboard }] },
  {
    section: "Products",
    items: [
      { label: "Planning", path: "/planning", icon: Lightbulb },
      { label: "Development", path: "/development", icon: Code2 },
      { label: "Testing", path: "/testing", icon: FlaskConical },
      { label: "Review", path: "/review", icon: Search },
    ],
  },
  {
    section: "Workflows",
    // Only ever starts a new workflow — there's no workflow-list route yet,
    // so the label says exactly what it does rather than implying a browsable
    // index (existing workflows are on the Dashboard or reachable via a run).
    items: [{ label: "New Workflow", path: "/workflows/new", icon: GitMerge }],
  },
  {
    section: "History",
    items: [{ label: "All Runs", path: "/runs", icon: History }],
  },
  {
    section: "Administration",
    items: [
      { label: "Pull Requests", path: "/pull-requests", icon: GitPullRequest },
      { label: "Repositories", path: "/repositories", icon: FolderGit2 },
      { label: "Architecture", path: "/architecture", icon: Network },
      { label: "Reports", path: "/reports", icon: FileBarChart },
      { label: "Settings", path: "/settings", icon: Settings },
    ],
  },
];

/** Flat view of NAV_SECTIONS, derived once — Topbar's page-title lookup
 * doesn't care about grouping, so it keeps using this exactly as before. */
export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);
