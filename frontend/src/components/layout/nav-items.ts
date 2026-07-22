import {
  LayoutDashboard,
  GitPullRequest,
  FolderGit2,
  Network,
  FileBarChart,
  Settings,
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
  { label: "Pull Requests", path: "/pull-requests", icon: GitPullRequest },
  { label: "Repositories", path: "/repositories", icon: FolderGit2 },
  { label: "Architecture", path: "/architecture", icon: Network },
  { label: "Reports", path: "/reports", icon: FileBarChart },
  { label: "Settings", path: "/settings", icon: Settings },
];
