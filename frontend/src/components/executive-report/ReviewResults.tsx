import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { CollapsibleSection } from "./CollapsibleSection";
import type { ReviewRow } from "../../lib/executiveReportMapper";

interface ReviewResultsProps {
  rows: ReviewRow[];
}

/**
 * Review results — Security, Architecture, Testing, Performance, and
 * Best Practices categories displayed as a compact table.
 */
export function ReviewResults({ rows }: ReviewResultsProps) {
  if (rows.length === 0) return null;

  return (
    <CollapsibleSection title="Review Results">
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[0.65rem] uppercase tracking-wide text-fg-muted">
                <th className="px-2 py-2 font-medium">Category</th>
                <th className="px-2 py-2 font-medium">Status</th>
                <th className="px-2 py-2 font-medium">Summary</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-muted">
              {rows.map((row) => (
                <tr key={row.category} className="text-fg-secondary">
                  <td className="px-2 py-2 font-medium text-fg">{row.category}</td>
                  <td className="px-2 py-2">
                    <StatusBadge label={row.status} tone={row.statusTone} />
                  </td>
                  <td className="max-w-xs truncate px-2 py-2 text-fg-muted">{row.summary}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </CollapsibleSection>
  );
}
