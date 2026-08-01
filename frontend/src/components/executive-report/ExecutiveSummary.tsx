import { Clock, DollarSign, Hash, TrendingUp } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { CollapsibleSection } from "./CollapsibleSection";
import type { SummaryProps } from "../../lib/executiveReportMapper";

interface ExecutiveSummaryProps {
  data: SummaryProps;
}

/**
 * Executive Summary section — workflow name, status, duration, AI cost,
 * tokens, and confidence score at a glance.
 */
export function ExecutiveSummary({ data }: ExecutiveSummaryProps) {
  return (
    <CollapsibleSection title="Executive Summary">
      <Card>
        <div className="mb-4">
          <h3 className="font-display text-lg font-semibold text-fg">{data.title}</h3>
          {data.description && (
            <p className="mt-1 text-xs text-fg-muted line-clamp-2">{data.description}</p>
          )}
          {data.approvedBy && (
            <p className="mt-1 text-xs text-fg-muted">
              Approved by <span className="font-medium text-fg-secondary">{data.approvedBy}</span>
            </p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            icon={TrendingUp}
            label="Status"
            value={<StatusBadge label={data.status} tone={data.statusTone} />}
          />
          <StatTile icon={Clock} label="Duration" value={data.duration} />
          <StatTile icon={DollarSign} label="AI Cost" value={data.cost} hint={`${data.tokens} tokens`} />
          <StatTile icon={Hash} label="Confidence" value={data.confidence} />
        </div>
      </Card>
    </CollapsibleSection>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg bg-surface-raised px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <Icon className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
        <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
          {label}
        </span>
      </div>
      <div className="mt-1 font-mono text-lg font-semibold tabular-nums text-fg">
        {typeof value === "string" ? value : value}
      </div>
      {hint && <p className="text-[0.65rem] text-fg-muted">{hint}</p>}
    </div>
  );
}
