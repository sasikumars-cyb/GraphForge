import { Terminal, Flag, Activity, Bug } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";

interface AdvancedItemProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  status: string;
  statusTone: "success" | "neutral" | "warning" | "info";
}

function AdvancedItem({ icon, title, description, status, statusTone }: AdvancedItemProps) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-surface-raised text-fg-muted">
          {icon}
        </div>
        <div>
          <p className="text-sm font-medium text-fg-secondary">{title}</p>
          <p className="text-xs text-fg-muted">{description}</p>
        </div>
      </div>
      <StatusBadge label={status} tone={statusTone} />
    </div>
  );
}

export function AdvancedSection() {
  return (
    <div className="flex flex-col gap-5">
      <Card
        title="Diagnostics"
        description="Platform health and debugging tools"
      >
        <div className="divide-y divide-line-muted">
          <AdvancedItem
            icon={<Activity className="h-4 w-4" />}
            title="Provider Debugging"
            description="Detailed logging for AI provider requests and responses"
            status="Disabled"
            statusTone="neutral"
          />
          <AdvancedItem
            icon={<Terminal className="h-4 w-4" />}
            title="Agent Execution Logs"
            description="Step-by-step trace of agent reasoning and tool usage"
            status="Available"
            statusTone="info"
          />
          <AdvancedItem
            icon={<Bug className="h-4 w-4" />}
            title="Error Reporting"
            description="Structured error collection for failed workflows and agent runs"
            status="Active"
            statusTone="success"
          />
        </div>
      </Card>

      <Card
        title="Feature Flags"
        description="Experimental features and preview capabilities"
      >
        <div className="divide-y divide-line-muted">
          <AdvancedItem
            icon={<Flag className="h-4 w-4" />}
            title="Streaming Responses"
            description="Stream AI provider responses for real-time progress in the UI"
            status="Preview"
            statusTone="warning"
          />
          <AdvancedItem
            icon={<Flag className="h-4 w-4" />}
            title="Multi-Agent Orchestration"
            description="Enable parallel agent execution for independent workflow stages"
            status="Stable"
            statusTone="success"
          />
          <AdvancedItem
            icon={<Flag className="h-4 w-4" />}
            title="Knowledge Graph RAG"
            description="Use Neo4j architecture graph for retrieval-augmented generation"
            status="Preview"
            statusTone="warning"
          />
        </div>
      </Card>

      <Card
        title="Telemetry"
        description="Usage metrics and platform observability"
      >
        <p className="text-sm text-fg-muted">
          GraphForge collects anonymous usage metrics to improve agent performance and reliability.
          No code, prompts, or business data is transmitted. Telemetry can be disabled via the
          environment variable <code className="rounded bg-surface-raised px-1 py-0.5 text-xs text-fg-secondary">GRAPHFORGE_TELEMETRY=off</code>.
        </p>
      </Card>
    </div>
  );
}
