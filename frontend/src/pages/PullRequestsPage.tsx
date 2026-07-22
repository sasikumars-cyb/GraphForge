import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { mockPullRequests } from "../lib/mock/pullRequests";
import { pullRequestStatusPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import type { PullRequest } from "../types/domain";

const columns: TableColumn<PullRequest>[] = [
  {
    key: "title",
    header: "Pull request",
    render: (pr) => (
      <div>
        <p className="font-medium text-slate-100">{pr.title}</p>
        <p className="text-xs text-slate-500">#{pr.id.replace("pr-", "")}</p>
      </div>
    ),
  },
  { key: "repository", header: "Repository", render: (pr) => pr.repository },
  { key: "author", header: "Author", render: (pr) => pr.author },
  {
    key: "status",
    header: "Status",
    render: (pr) => {
      const { label, tone } = pullRequestStatusPresentation(pr.status);
      return <StatusBadge label={label} tone={tone} />;
    },
  },
  { key: "risk", header: "Risk", render: (pr) => <RiskBadge level={pr.risk} /> },
  {
    key: "affected",
    header: "Affected services",
    render: (pr) => (pr.affectedServices === 0 ? "—" : pr.affectedServices),
  },
  { key: "updated", header: "Updated", render: (pr) => formatRelativeTime(pr.updatedAt) },
];

export function PullRequestsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Pull Requests</h2>
        <p className="mt-1 text-sm text-slate-400">
          Every tracked pull request across monitored repositories, with its computed risk.
        </p>
      </div>

      <Card>
        <Table columns={columns} data={mockPullRequests} getRowKey={(pr) => pr.id} />
      </Card>
    </div>
  );
}
