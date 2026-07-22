import { StatCard } from "../components/StatCard";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { mockDashboardStats } from "../lib/mock/dashboardStats";
import { mockPullRequests } from "../lib/mock/pullRequests";
import { mockRepositories } from "../lib/mock/repositories";
import {
  pullRequestStatusPresentation,
  repositoryHealthPresentation,
} from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import type { PullRequest, Repository } from "../types/domain";
import { LayoutDashboard, FolderGit2, GitPullRequest, Clock } from "lucide-react";

const STAT_ICONS = [FolderGit2, GitPullRequest, LayoutDashboard, Clock];

const recentPullRequestColumns: TableColumn<PullRequest>[] = [
  {
    key: "title",
    header: "Pull request",
    render: (pr) => (
      <div>
        <p className="font-medium text-slate-100">{pr.title}</p>
        <p className="text-xs text-slate-500">{pr.repository}</p>
      </div>
    ),
  },
  { key: "risk", header: "Risk", render: (pr) => <RiskBadge level={pr.risk} /> },
  {
    key: "status",
    header: "Status",
    render: (pr) => {
      const { label, tone } = pullRequestStatusPresentation(pr.status);
      return <StatusBadge label={label} tone={tone} />;
    },
  },
  { key: "updated", header: "Updated", render: (pr) => formatRelativeTime(pr.updatedAt) },
];

const repositoryColumns: TableColumn<Repository>[] = [
  { key: "name", header: "Repository", render: (repo) => repo.name },
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
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Dashboard</h2>
        <p className="mt-1 text-sm text-slate-400">
          An overview of monitored repositories and their most recent analyzed changes.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {mockDashboardStats.map((stat, index) => (
          <StatCard
            key={stat.label}
            label={stat.label}
            value={stat.value}
            hint={stat.hint}
            icon={STAT_ICONS[index]}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card
          title="Recent pull requests"
          description="Latest changes analyzed across all repositories"
          className="lg:col-span-2"
        >
          <Table
            columns={recentPullRequestColumns}
            data={mockPullRequests.slice(0, 5)}
            getRowKey={(pr) => pr.id}
          />
        </Card>

        <Card title="Repositories at a glance" description="Current health status">
          <Table
            columns={repositoryColumns}
            data={mockRepositories}
            getRowKey={(repo) => repo.id}
          />
        </Card>
      </div>
    </div>
  );
}
