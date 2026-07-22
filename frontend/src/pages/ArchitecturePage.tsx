import { Card } from "../components/Card";
import { Table, type TableColumn } from "../components/Table";
import { RiskBadge } from "../components/RiskBadge";
import { mockServiceNodes } from "../lib/mock/architecture";
import type { ServiceNode } from "../types/domain";
import { Network } from "lucide-react";

const columns: TableColumn<ServiceNode>[] = [
  { key: "name", header: "Service", render: (svc) => svc.name },
  { key: "repository", header: "Repository", render: (svc) => svc.repository },
  { key: "dependents", header: "Dependents", render: (svc) => svc.dependents },
  { key: "dependencies", header: "Dependencies", render: (svc) => svc.dependencies },
  { key: "risk", header: "Risk", render: (svc) => <RiskBadge level={svc.risk} /> },
];

export function ArchitecturePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Architecture</h2>
        <p className="mt-1 text-sm text-slate-400">
          The dependency graph ChangeGuard builds from your codebase — shown here with sample
          services until a repository is indexed.
        </p>
      </div>

      <Card
        title="Dependency graph"
        description="Interactive graph rendering isn't implemented yet — this is a preview of the layout."
      >
        <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
          <Network className="h-8 w-8" aria-hidden="true" />
          <p className="text-sm">Graph visualization will render here</p>
        </div>
      </Card>

      <Card title="Services & dependencies" description="Nodes and edge counts in the sample graph">
        <Table columns={columns} data={mockServiceNodes} getRowKey={(svc) => svc.id} />
      </Card>
    </div>
  );
}
