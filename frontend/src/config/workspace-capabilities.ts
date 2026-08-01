/**
 * Registry of all AI capabilities available in the AI Workspace.
 *
 * Adding a new capability requires ONLY adding an entry here — no sidebar
 * changes, no route changes, no layout changes.  The WorkspacePage renders
 * this list automatically.
 */

import {
  Lightbulb,
  Code2,
  FlaskConical,
  GitPullRequestArrow,
  ShieldCheck,
  FileText,
  ArrowRightLeft,
  Compass,
  type LucideIcon,
} from "lucide-react";

export type CapabilityCategory = "plan" | "build" | "test" | "review" | "explore";

export interface WorkspaceCapability {
  /** URL slug — becomes /workspace/:slug */
  slug: string;
  /** Display name */
  name: string;
  /** One-line description shown in the catalog card */
  description: string;
  /** Lucide icon component */
  icon: LucideIcon;
  /** Tailwind color token (e.g. "sky", "emerald") for theming */
  color: string;
  /** Category for filtering */
  category: CapabilityCategory;
  /** Whether this capability is available for use */
  available: boolean;
  /** Backend goal string (passed to useAgentRun) */
  goal?: string;
}

export const WORKSPACE_CAPABILITIES: WorkspaceCapability[] = [
  {
    slug: "planning",
    name: "Planning",
    description:
      "Generate architecture-grounded implementation plans backed by verifiable evidence.",
    icon: Lightbulb,
    color: "sky",
    category: "plan",
    available: true,
    goal: "plan_freeform",
  },
  {
    slug: "development",
    name: "Development",
    description:
      "Create detailed implementation blueprints with repository changes, phases, and risks.",
    icon: Code2,
    color: "violet",
    category: "build",
    available: true,
    goal: "develop_change_plan",
  },
  {
    slug: "testing",
    name: "Testing",
    description:
      "Generate comprehensive test strategies covering unit, integration, and edge cases.",
    icon: FlaskConical,
    color: "amber",
    category: "test",
    available: true,
    goal: "plan_tests",
  },
  {
    slug: "pr-review",
    name: "PR Review",
    description:
      "Analyze GitHub pull requests for change impact, breaking changes, and blast radius.",
    icon: GitPullRequestArrow,
    color: "emerald",
    category: "review",
    available: true,
    goal: "review_pr",
  },
  {
    slug: "security-review",
    name: "Security Review",
    description: "Identify security vulnerabilities, dependency risks, and compliance gaps.",
    icon: ShieldCheck,
    color: "rose",
    category: "review",
    available: false,
  },
  {
    slug: "documentation",
    name: "Documentation",
    description: "Review a repository's Markdown docs against its indexed architecture and propose updates.",
    icon: FileText,
    color: "teal",
    category: "build",
    available: true,
    goal: "review_documentation",
  },
  {
    slug: "migration-assistant",
    name: "Migration Assistant",
    description: "Plan and validate technology migrations with dependency-aware impact analysis.",
    icon: ArrowRightLeft,
    color: "orange",
    category: "plan",
    available: false,
  },
  {
    slug: "dependency-explorer",
    name: "Dependency Explorer",
    description: "Traverse and visualize service dependencies, data flows, and blast radius.",
    icon: Compass,
    color: "indigo",
    category: "explore",
    available: false,
  },
];

export const CATEGORY_LABELS: Record<CapabilityCategory, string> = {
  plan: "Plan",
  build: "Build",
  test: "Test",
  review: "Review",
  explore: "Explore",
};
