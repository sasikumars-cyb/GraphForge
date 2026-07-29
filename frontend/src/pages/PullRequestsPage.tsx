import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { formatRelativeTime } from "../lib/formatDate";
import { usePullRequestsData, type PullRequestRow } from "../hooks/usePullRequestsData";

const columns: TableColumn<PullRequestRow>[] = [
  {
    key: "title",
    header: "Pull request",
    render: (pr) => (
      <Link to={`/pull-requests/${pr.id}`} className="block hover:underline">
        <p className="font-medium text-fg">{pr.title}</p>
        <p className="text-xs text-fg-muted">#{pr.number}</p>
      </Link>
    ),
  },
  { key: "repository", header: "Repository", render: (pr) => pr.repositoryFullName },
  { key: "author", header: "Author", render: (pr) => pr.authorLogin },
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
  {
    key: "risk",
    header: "Risk",
    render: (pr) =>
      pr.risk ? <RiskBadge level={pr.risk} /> : <StatusBadge label="Not analyzed" tone="neutral" />,
  },
  { key: "updated", header: "Updated", render: (pr) => formatRelativeTime(pr.updatedAt) },
];

export function PullRequestsPage() {
  const { pullRequests, isLoading, error } = usePullRequestsData();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-fg">Pull Requests</h2>
        <p className="mt-1 text-sm text-fg-muted">
          Every tracked pull request across monitored repositories, with its computed risk.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      <Card>
        <Table
          columns={columns}
          data={pullRequests}
          getRowKey={(pr) => pr.id}
          emptyMessage={isLoading ? "Loading…" : "No pull requests yet."}
        />
      </Card>
    </div>
  );
}
