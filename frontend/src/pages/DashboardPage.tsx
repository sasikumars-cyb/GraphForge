import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { StatCard } from "../components/StatCard";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { WorkflowTimeline } from "../components/workflow/WorkflowTimeline";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import {
  useDashboardData,
  type DashboardPullRequestRow,
  type DashboardRepositoryRow,
} from "../hooks/useDashboardData";
import { useAuth } from "../app/auth-context";
import { listWorkflows } from "../lib/api/workflows";
import type { WorkflowListItem } from "../types/agent";
import {
  LayoutDashboard,
  FolderGit2,
  GitPullRequest,
  Clock,
  Lightbulb,
  Search,
  GitMerge,
} from "lucide-react";

const recentPullRequestColumns: TableColumn<DashboardPullRequestRow>[] = [
  {
    key: "title",
    header: "Pull request",
    render: (pr) => (
      <Link to={`/pull-requests/${pr.id}`} className="block hover:underline">
        <p className="font-medium text-slate-100">{pr.title}</p>
        <p className="text-xs text-slate-500">{pr.repositoryFullName}</p>
      </Link>
    ),
  },
  {
    key: "risk",
    header: "Risk",
    render: (pr) =>
      pr.risk ? <RiskBadge level={pr.risk} /> : <StatusBadge label="Not analyzed" tone="neutral" />,
  },
  {
    key: "status",
    header: "Status",
    render: (pr) => (
      <StatusBadge
        label={pr.isDraft ? "Draft" : pr.state === "open" ? "Open" : pr.state}
        tone={pr.state === "open" ? (pr.isDraft ? "neutral" : "info") : "success"}
      />
    ),
  },
  { key: "updated", header: "Updated", render: (pr) => formatRelativeTime(pr.updatedAt) },
];

const repositoryColumns: TableColumn<DashboardRepositoryRow>[] = [
  {
    key: "name",
    header: "Repository",
    render: (repo) => (
      <Link to={`/repositories/${repo.id}`} className="hover:underline">
        {repo.name}
      </Link>
    ),
  },
  {
    key: "health",
    header: "Health",
    render: (repo) => {
      const { label, tone } = repositoryHealthPresentation(repo.health);
      return <StatusBadge label={label} tone={tone} />;
    },
  },
  { key: "openPrs", header: "Open PRs", render: (repo) => repo.openPullRequests },
];

export function DashboardPage() {
  const { stats, recentPullRequests, repositories, isLoading, error } = useDashboardData();
  const { token } = useAuth();
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listWorkflows(token, { page_size: 5 })
      .then((res) => setWorkflows(res.items))
      .catch((err) => {
        setWorkflowsError(err instanceof Error ? err.message : "Failed to load active workflows.");
      });
  }, [token]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Welcome back to <span className="text-brand-400">GraphForge</span>
        </h2>
        <p className="mt-1.5 max-w-2xl text-sm text-slate-400">
          AI engineering intelligence — every claim grounded in your Knowledge Graph, every decision
          backed by evidence.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Active Workflows */}
      {workflowsError && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {workflowsError}
        </div>
      )}
      {workflows.length > 0 && (
        <Card
          title="Active SDLC Workflows"
          description="Engineering tasks progressing through the lifecycle"
          action={
            <Link
              to="/workflows/new"
              className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
            >
              + New Workflow
            </Link>
          }
        >
          <div className="space-y-4">
            {workflows.map((w) => (
              <Link
                key={w.workflow_id}
                to={`/workflows/${w.workflow_id}`}
                className="block rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition-colors hover:border-indigo-500/40 hover:bg-slate-900/60"
              >
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="text-sm font-medium text-slate-100 truncate">{w.title}</h4>
                  <StatusBadge
                    label={w.status === "completed" ? "Complete" : "In Progress"}
                    tone={w.status === "completed" ? "success" : "info"}
                  />
                </div>
                <WorkflowTimeline stages={w.stages} currentStage={w.current_stage} />
              </Link>
            ))}
          </div>
        </Card>
      )}

      {/* Agent Actions */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Link
          to="/workflows/new"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-indigo-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          aria-label="Start SDLC Workflow"
        >
          <div className="rounded-lg bg-indigo-500/10 p-3 ring-1 ring-inset ring-indigo-500/30">
            <GitMerge className="h-6 w-6 text-indigo-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-indigo-300">
              SDLC Workflow
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Start a guided engineering lifecycle. Planning → Development → Testing → Review — each
              phase feeds the next.
            </p>
          </div>
        </Link>

        <Link
          to="/planning"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-sky-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
          aria-label="Planning Assistant"
        >
          <div className="rounded-lg bg-sky-500/10 p-3 ring-1 ring-inset ring-sky-500/30">
            <Lightbulb className="h-6 w-6 text-sky-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-sky-300">
              Planning Assistant
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Describe an engineering task and get an AI-generated implementation plan grounded in
              your architecture graph. Every recommendation comes with verifiable evidence.
            </p>
          </div>
        </Link>

        <Link
          to="/review"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-emerald-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
          aria-label="Review Pull Request"
        >
          <div className="rounded-lg bg-emerald-500/10 p-3 ring-1 ring-inset ring-emerald-500/30">
            <Search className="h-6 w-6 text-emerald-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-emerald-300">
              Review Pull Request
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Submit a GitHub PR URL for AI-powered change impact analysis. Get breaking changes,
              blast radius, and reviewers — all backed by graph evidence, never hallucinated.
            </p>
          </div>
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Repositories monitored"
          value={isLoading ? "—" : String(stats.repositoriesMonitored)}
          hint={`across ${stats.organizationCount} organization${stats.organizationCount === 1 ? "" : "s"}`}
          icon={FolderGit2}
        />
        <StatCard
          label="Open pull requests"
          value={isLoading ? "—" : String(stats.openPullRequestCount)}
          hint={`${stats.awaitingAnalysisCount} awaiting analysis`}
          icon={GitPullRequest}
        />
        <StatCard
          label="High risk changes"
          value={isLoading ? "—" : String(stats.highRiskThisWeekCount)}
          hint="critical or high this week"
          icon={LayoutDashboard}
        />
        <StatCard
          label="Avg. indexing time"
          value={isLoading ? "—" : stats.avgIndexingTimeLabel}
          hint="per repository"
          icon={Clock}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card
          title="Recent pull requests"
          description="Latest changes analyzed across all repositories"
          className="lg:col-span-2"
        >
          <Table
            columns={recentPullRequestColumns}
            data={recentPullRequests.slice(0, 5)}
            getRowKey={(pr) => pr.id}
            emptyMessage={isLoading ? "Loading…" : "No pull requests yet."}
          />
        </Card>

        <Card title="Repositories at a glance" description="Current health status">
          <Table
            columns={repositoryColumns}
            data={repositories}
            getRowKey={(repo) => repo.id}
            emptyMessage={isLoading ? "Loading…" : "No repositories tracked yet."}
          />
        </Card>
      </div>
    </div>
  );
}
