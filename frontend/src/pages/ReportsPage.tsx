import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { mockReports } from "../lib/mock/reports";
import { reportStatusPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import type { Report } from "../types/domain";
import { Download } from "lucide-react";

const columns: TableColumn<Report>[] = [
  { key: "name", header: "Report", render: (report) => report.name },
  { key: "repository", header: "Repository", render: (report) => report.repository },
  { key: "risk", header: "Risk", render: (report) => <RiskBadge level={report.risk} /> },
  {
    key: "status",
    header: "Status",
    render: (report) => {
      const { label, tone } = reportStatusPresentation(report.status);
      return <StatusBadge label={label} tone={tone} />;
    },
  },
  {
    key: "generatedAt",
    header: "Generated",
    render: (report) => formatRelativeTime(report.generatedAt),
  },
  {
    key: "actions",
    header: "",
    className: "text-right",
    render: (report) => (
      <button
        type="button"
        disabled={report.status !== "ready"}
        title={report.status === "ready" ? "Download report" : "Not available yet"}
        className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-sky-300 hover:bg-sky-500/10 disabled:cursor-not-allowed disabled:text-slate-600 disabled:hover:bg-transparent"
      >
        <Download className="h-3.5 w-3.5" aria-hidden="true" />
        Download
      </button>
    ),
  },
];

export function ReportsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Reports</h2>
        <p className="mt-1 text-sm text-slate-400">
          Change evidence packets generated for reviewed pull requests.
        </p>
      </div>

      <Card>
        <Table columns={columns} data={mockReports} getRowKey={(report) => report.id} />
      </Card>
    </div>
  );
}
