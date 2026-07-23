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

/** Single source of truth for sidebar links and the topbar's page title. */
export const NAV_ITEMS: NavItem[] = [
  { label: "Dashboard", path: "/", icon: LayoutDashboard },
  { label: "Workflows", path: "/workflows/new", icon: GitMerge },
  { label: "Planning", path: "/planning", icon: Lightbulb },
  { label: "Development", path: "/development", icon: Code2 },
  { label: "Testing", path: "/testing", icon: FlaskConical },
  { label: "Review", path: "/review", icon: Search },
  { label: "Run History", path: "/runs", icon: History },
  { label: "Pull Requests", path: "/pull-requests", icon: GitPullRequest },
  { label: "Repositories", path: "/repositories", icon: FolderGit2 },
  { label: "Architecture", path: "/architecture", icon: Network },
  { label: "Reports", path: "/reports", icon: FileBarChart },
  { label: "Settings", path: "/settings", icon: Settings },
];
