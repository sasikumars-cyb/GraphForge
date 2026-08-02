import { useEffect, useState, type ReactNode } from "react";
import { GitCompare, RefreshCcw } from "lucide-react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { Table, type TableColumn } from "../components/Table";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import { getRepositoryParity } from "../lib/api/parity";
import type { TrackedRepository } from "../types/github";
import type {
  DuplicateEntity,
  EdgePropertyMismatch,
  EdgeSignature,
  IgnoredDifference,
  NodeMismatch,
  ParityReport,
} from "../types/parity";

/**
 * Read-only Graph Parity dashboard. Every field shown here comes straight
 * from `GET /repositories/{id}/parity` (backend: `app.services
 * .parity_service.run_parity_check`, which only ever calls
 * `IGraphRepository.get_full_graph` and `materialize_repository_graph` —
 * both reads). This page never writes to Neo4j, never triggers indexing,
 * and never implements any part of Shadow Mode or Production Cutover — it
 * only visualizes what the (unmodified) Graph Parity Engine already
 * computed.
 */
export function GraphParityPage() {
  const { token } = useAuth();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [reposError, setReposError] = useState<string | null>(null);

  const [report, setReport] = useState<ParityReport | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listTrackedRepositories(token)
      .then((repos) => {
        if (!cancelled) setRepositories(repos);
      })
      .catch((err) => {
        if (!cancelled) {
          setReposError(err instanceof Error ? err.message : "Could not load repositories.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const runCheck = () => {
    if (!token || !selectedRepoId) return;
    setIsRunning(true);
    setRunError(null);
    setReport(null);
    getRepositoryParity(token, selectedRepoId)
      .then(setReport)
      .catch((err) => {
        setRunError(err instanceof Error ? err.message : "Parity check failed.");
      })
      .finally(() => setIsRunning(false));
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-cat-4-bg p-2 ring-1 ring-inset ring-cat-4-line/30">
          <GitCompare className="h-5 w-5 text-cat-4-fg" aria-hidden="true" />
        </div>
        <div>
          <h2 className="text-xl font-semibold text-fg">Graph Parity</h2>
          <p className="text-sm text-fg-muted">
            Compares the live Neo4j graph against the Engineering Memory projection — read-only, no
            writes to Neo4j, no indexing triggered.
          </p>
        </div>
      </div>

      <Card title="Run a parity check">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-fg-muted">
            Repository
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              disabled={loadingRepos}
              className="min-w-64 rounded-lg border border-line-muted bg-surface px-3 py-2 text-sm text-fg"
            >
              <option value="">
                {loadingRepos ? "Loading repositories…" : "Select a repository"}
              </option>
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={runCheck}
            disabled={!selectedRepoId || isRunning}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCcw
              className={`h-3.5 w-3.5 ${isRunning ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            {isRunning ? "Running…" : "Run Parity Check"}
          </button>
        </div>
        {reposError && <p className="mt-3 text-sm text-danger-fg">{reposError}</p>}
        {runError && <p className="mt-3 text-sm text-danger-fg">{runError}</p>}
      </Card>

      {report && <ParityReportView report={report} />}
    </div>
  );
}

function ParityReportView({ report }: { report: ParityReport }) {
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <StatusBadge
              label={report.overall_result === "pass" ? "PASS" : "FAIL"}
              tone={report.overall_result === "pass" ? "success" : "danger"}
            />
            <p className="text-sm text-fg-secondary">{report.summary}</p>
          </div>
          <div className="text-right">
            <p className="text-2xl font-semibold text-fg">
              {report.similarity_percentage.toFixed(2)}%
            </p>
            <p className="text-xs text-fg-muted">similarity</p>
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <StatisticsCard title="Node statistics" stats={report.node_statistics} />
        <StatisticsCard title="Edge statistics" stats={report.edge_statistics} />
      </div>

      <MismatchSection
        title="Missing nodes"
        description="Present in the legacy graph, absent from the materialized projection."
        count={report.missing_nodes.length}
      >
        <Table
          columns={[{ key: "id", header: "Node ID", render: (id: string) => id }]}
          data={report.missing_nodes}
          getRowKey={(id) => id}
          emptyMessage="No missing nodes."
        />
      </MismatchSection>

      <MismatchSection
        title="Unexpected nodes"
        description="Present in the materialized projection, absent from the legacy graph."
        count={report.unexpected_nodes.length}
      >
        <Table
          columns={[{ key: "id", header: "Node ID", render: (id: string) => id }]}
          data={report.unexpected_nodes}
          getRowKey={(id) => id}
          emptyMessage="No unexpected nodes."
        />
      </MismatchSection>

      <MismatchSection
        title="Node mismatches"
        description="Present on both sides, with a label or property difference."
        count={report.node_mismatches.length}
      >
        <NodeMismatchList mismatches={report.node_mismatches} />
      </MismatchSection>

      <MismatchSection
        title="Missing edges"
        description="Present in the legacy graph, absent from the materialized projection."
        count={report.missing_edges.length}
      >
        <EdgeSignatureTable edges={report.missing_edges} />
      </MismatchSection>

      <MismatchSection
        title="Unexpected edges"
        description="Present in the materialized projection, absent from the legacy graph."
        count={report.unexpected_edges.length}
      >
        <EdgeSignatureTable edges={report.unexpected_edges} />
      </MismatchSection>

      <MismatchSection
        title="Edge property mismatches"
        description="Same source/type/target on both sides, different properties."
        count={report.edge_property_mismatches.length}
      >
        <EdgePropertyMismatchList mismatches={report.edge_property_mismatches} />
      </MismatchSection>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <MismatchSection
          title="Duplicate nodes"
          description="A node id appearing more than once on one or both sides."
          count={report.duplicate_nodes.length}
        >
          <DuplicateTable duplicates={report.duplicate_nodes} entityLabel="Node ID" />
        </MismatchSection>

        <MismatchSection
          title="Duplicate edges"
          description="A source/type/target triple appearing more than once on one or both sides."
          count={report.duplicate_edges.length}
        >
          <DuplicateTable duplicates={report.duplicate_edges} entityLabel="Edge triple" />
        </MismatchSection>
      </div>

      <MismatchSection
        title="Ignored differences"
        description="Properties excluded from comparison by the Graph Parity Engine's configurable ignore rules — expected differences, not defects."
        count={report.ignored_differences.length}
        tone="neutral"
      >
        <IgnoredDifferenceTable ignored={report.ignored_differences} />
      </MismatchSection>
    </div>
  );
}

function StatisticsCard({
  title,
  stats,
}: {
  title: string;
  stats: { legacy_count: number; materialized_count: number; matched_count: number };
}) {
  return (
    <Card title={title}>
      <dl className="grid grid-cols-3 gap-4 text-center">
        <div>
          <dt className="text-xs text-fg-muted">Legacy</dt>
          <dd className="text-lg font-semibold text-fg">{stats.legacy_count}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-muted">Materialized</dt>
          <dd className="text-lg font-semibold text-fg">{stats.materialized_count}</dd>
        </div>
        <div>
          <dt className="text-xs text-fg-muted">Matched</dt>
          <dd className="text-lg font-semibold text-success-fg">{stats.matched_count}</dd>
        </div>
      </dl>
    </Card>
  );
}

function MismatchSection({
  title,
  description,
  count,
  tone = "danger",
  children,
}: {
  title: string;
  description: string;
  count: number;
  tone?: "danger" | "neutral";
  children: ReactNode;
}) {
  const isEmpty = count === 0;
  return (
    <details className="rounded-xl border border-line-muted bg-surface shadow-sm" open={!isEmpty}>
      <summary className="flex cursor-pointer items-center justify-between gap-4 px-5 py-4">
        <div>
          <span className="font-display text-sm font-semibold tracking-tight text-fg">{title}</span>
          <p className="mt-0.5 text-xs text-fg-muted">{description}</p>
        </div>
        <StatusBadge
          label={String(count)}
          tone={isEmpty ? "success" : tone === "neutral" ? "neutral" : "danger"}
        />
      </summary>
      <div className="border-t border-line-muted p-5">{children}</div>
    </details>
  );
}

function NodeMismatchList({ mismatches }: { mismatches: NodeMismatch[] }) {
  if (mismatches.length === 0) {
    return <p className="text-sm text-fg-muted">No node mismatches.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {mismatches.map((mismatch) => (
        <details key={mismatch.node_id} className="rounded-lg border border-line-muted p-3">
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {mismatch.node_id}
          </summary>
          <div className="mt-2 flex flex-col gap-2 text-xs">
            {mismatch.label_differences.length > 0 && (
              <ul className="list-inside list-disc text-fg-secondary">
                {mismatch.label_differences.map((diff) => (
                  <li key={diff}>{diff}</li>
                ))}
              </ul>
            )}
            <PropertyDiffTable differences={mismatch.property_differences} />
          </div>
        </details>
      ))}
    </div>
  );
}

type KeyedEdgeSignature = EdgeSignature & { _rowKey: string };

function EdgeSignatureTable({ edges }: { edges: EdgeSignature[] }) {
  const keyed: KeyedEdgeSignature[] = edges.map((edge, index) => ({
    ...edge,
    _rowKey: `${edge.source_id}-${edge.type}-${edge.target_id}-${index}`,
  }));
  const columns: TableColumn<KeyedEdgeSignature>[] = [
    { key: "source", header: "Source", render: (e) => e.source_id },
    { key: "type", header: "Type", render: (e) => e.type },
    { key: "target", header: "Target", render: (e) => e.target_id },
    {
      key: "properties",
      header: "Properties",
      render: (e) => <code className="text-xs text-fg-muted">{e.properties_json}</code>,
    },
  ];
  return (
    <Table
      columns={columns}
      data={keyed}
      getRowKey={(e) => e._rowKey}
      emptyMessage="No edges to show."
    />
  );
}

function EdgePropertyMismatchList({ mismatches }: { mismatches: EdgePropertyMismatch[] }) {
  if (mismatches.length === 0) {
    return <p className="text-sm text-fg-muted">No edge property mismatches.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {mismatches.map((mismatch, index) => (
        <details
          key={`${mismatch.source_id}-${mismatch.type}-${mismatch.target_id}-${index}`}
          className="rounded-lg border border-line-muted p-3"
        >
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {mismatch.source_id} —[{mismatch.type}]→ {mismatch.target_id}
          </summary>
          <div className="mt-2">
            <PropertyDiffTable differences={mismatch.property_differences} />
          </div>
        </details>
      ))}
    </div>
  );
}

function PropertyDiffTable({
  differences,
}: {
  differences: { key: string; legacy_value: string | null; materialized_value: string | null }[];
}) {
  if (differences.length === 0) return null;
  const columns: TableColumn<(typeof differences)[number]>[] = [
    { key: "key", header: "Property", render: (d) => d.key },
    {
      key: "legacy",
      header: "Legacy",
      render: (d) => <code className="text-xs">{d.legacy_value ?? "(absent)"}</code>,
    },
    {
      key: "materialized",
      header: "Materialized",
      render: (d) => <code className="text-xs">{d.materialized_value ?? "(absent)"}</code>,
    },
  ];
  return <Table columns={columns} data={differences} getRowKey={(d) => d.key} />;
}

function DuplicateTable({
  duplicates,
  entityLabel,
}: {
  duplicates: DuplicateEntity[];
  entityLabel: string;
}) {
  const columns: TableColumn<DuplicateEntity>[] = [
    { key: "key", header: entityLabel, render: (d) => d.key },
    { key: "legacy", header: "Legacy count", render: (d) => String(d.legacy_count) },
    {
      key: "materialized",
      header: "Materialized count",
      render: (d) => String(d.materialized_count),
    },
  ];
  return (
    <Table
      columns={columns}
      data={duplicates}
      getRowKey={(d) => d.key}
      emptyMessage="No duplicates detected."
    />
  );
}

type KeyedIgnoredDifference = IgnoredDifference & { _rowKey: string };

function IgnoredDifferenceTable({ ignored }: { ignored: IgnoredDifference[] }) {
  const keyed: KeyedIgnoredDifference[] = ignored.map((diff, index) => ({
    ...diff,
    _rowKey: `${diff.entity_key}-${diff.property_name}-${index}`,
  }));
  const columns: TableColumn<KeyedIgnoredDifference>[] = [
    { key: "kind", header: "Kind", render: (d) => d.entity_kind },
    { key: "entity", header: "Entity", render: (d) => d.entity_key },
    { key: "property", header: "Property", render: (d) => d.property_name },
    { key: "reason", header: "Reason", render: (d) => d.reason },
  ];
  return (
    <Table
      columns={columns}
      data={keyed}
      getRowKey={(d) => d._rowKey}
      emptyMessage="No ignored differences."
    />
  );
}
