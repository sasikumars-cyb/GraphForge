import { AlertTriangle, ArrowRight, ShieldAlert } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { CollapsibleSection } from "./CollapsibleSection";
import type { RecommendationsProps } from "../../lib/executiveReportMapper";

interface RecommendationsSectionProps {
  data: RecommendationsProps;
}

/**
 * Recommendations section — merge readiness, risks, blocking items, and
 * next actions.
 */
export function Recommendations({ data }: RecommendationsSectionProps) {
  const hasContent =
    data.mergeReadiness !== "not evaluated" ||
    data.risks.length > 0 ||
    data.nextActions.length > 0 ||
    data.blockingItems.length > 0;

  if (!hasContent) return null;

  return (
    <CollapsibleSection title="Recommendations">
      <Card>
        {/* Merge readiness badge */}
        <div className="mb-4 flex items-center gap-2">
          <span className="text-xs font-medium text-fg-muted">Merge Readiness:</span>
          <StatusBadge label={data.mergeReadiness} tone={data.mergeReadinessTone} />
        </div>

        {/* Blocking items */}
        {data.blockingItems.length > 0 && (
          <div className="mb-4">
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-warning-fg">
              <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
              Blocking Items
            </h3>
            <ul className="space-y-1.5">
              {data.blockingItems.map((item, i) => (
                <li
                  key={i}
                  className="rounded-md border-l-2 border-warning-line bg-warning-bg/30 px-3 py-1.5 text-xs text-fg-secondary"
                >
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Risks and actions side by side */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {data.risks.length > 0 && (
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-danger-fg">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                Risks
              </h3>
              <ul className="space-y-1.5">
                {data.risks.map((risk, i) => (
                  <li
                    key={i}
                    className="rounded-md border-l-2 border-danger-line bg-danger-bg/30 px-3 py-1.5 text-xs text-fg-secondary"
                  >
                    {risk}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.nextActions.length > 0 && (
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-info-fg">
                <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                Next Actions
              </h3>
              <ul className="space-y-1.5">
                {data.nextActions.map((action, i) => (
                  <li
                    key={i}
                    className="rounded-md border-l-2 border-info-line bg-info-bg/30 px-3 py-1.5 text-xs text-fg-secondary"
                  >
                    {action}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Card>
    </CollapsibleSection>
  );
}
