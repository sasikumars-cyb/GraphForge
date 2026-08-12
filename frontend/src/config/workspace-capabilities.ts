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
  HeartPulse,
  Network,
  GitCompare,
  BookOpen,
  Radar,
  Layers,
  ListTree,
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
  /** Omit from the AI Workspace catalog entirely — the route/page stays
   * live (other capabilities still deep-link into it, e.g. Migration
   * Assistant's "Create planning workflow"/"Validate migration"
   * actions), only the catalog card is hidden. Distinct from
   * `available: false` ("Coming Soon" — not built yet); this is "built,
   * but not surfaced as its own entry point." */
  hidden?: boolean;
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
    slug: "documentation-health",
    name: "Documentation Health",
    description:
      "Score a repository's Markdown documentation and get a health report — read-only, no changes made.",
    icon: HeartPulse,
    color: "teal",
    category: "review",
    available: true,
    goal: "analyze_documentation_health",
  },
  {
    slug: "api-intelligence",
    name: "API Intelligence",
    description:
      "Generate a visual API catalog and security review from Markdown documentation only — endpoints, OpenAPI, Postman, OWASP coverage.",
    icon: Network,
    color: "violet",
    category: "review",
    available: true,
    goal: "analyze_api_intelligence",
    hidden: true,
  },
  {
    slug: "graph-parity",
    name: "Graph Parity",
    description:
      "Compare the live Neo4j graph against the Engineering Memory projection — node/edge statistics, mismatches, and similarity — read-only, no writes to Neo4j.",
    icon: GitCompare,
    color: "indigo",
    category: "explore",
    available: true,
  },
  {
    slug: "repository-understanding",
    name: "Repository Understanding",
    description:
      "Explain what a repository does — its APIs, databases, queues, integrations, and dependencies — computed from the indexed graph. Read-only.",
    icon: BookOpen,
    color: "violet",
    category: "explore",
    available: true,
    goal: "analyze_repository_understanding",
  },
  {
    slug: "impact-analysis",
    name: "Impact Analysis",
    description:
      "Compute a repository's blast radius — impacted repositories, APIs, databases, and queues, with confidence per relationship — read-only.",
    icon: Radar,
    color: "orange",
    category: "explore",
    available: true,
    goal: "analyze_impact_analysis",
  },
  {
    slug: "dependency-query",
    name: "Dependency Query",
    description:
      "What does this repository depend on, and what depends on it — with confidence per relationship. Read-only.",
    icon: Layers,
    color: "teal",
    category: "explore",
    available: true,
    goal: "analyze_dependency_query",
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
    description:
      "Review a repository's Markdown docs against its indexed architecture and propose updates.",
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
    available: true,
  },
  {
    slug: "refinement-planner",
    name: "Refinement Planner",
    description: "Turn requirements into a refinement-ready engineering plan.",
    icon: ListTree,
    color: "sky",
    category: "plan",
    available: true,
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
