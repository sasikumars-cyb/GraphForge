import { GitBranch, FileText, Box } from "lucide-react";
import { Card } from "../Card";
import { CollapsibleSection } from "./CollapsibleSection";
import type { ImpactProps } from "../../lib/executiveReportMapper";

interface RepositoryImpactProps {
  data: ImpactProps;
}

/**
 * Repository impact section — files changed, repositories and components affected,
 * dependency impact.
 */
export function RepositoryImpact({ data }: RepositoryImpactProps) {
  const hasContent =
    data.repositoryCount > 0 || data.filesChanged > 0 || data.componentCount > 0;
  if (!hasContent) return null;

  return (
    <CollapsibleSection title="Repository Impact">
      <Card>
        {/* Stats row */}
        <div className="mb-4 grid grid-cols-3 gap-3">
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <GitBranch className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                Repositories
              </span>
            </div>
            <p className="mt-0.5 font-mono text-lg font-semibold text-fg">
              {data.repositoryCount}
            </p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <FileText className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                Files Changed
              </span>
            </div>
            <p className="mt-0.5 font-mono text-lg font-semibold text-fg">{data.filesChanged}</p>
          </div>
          <div className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-center gap-1.5">
              <Box className="h-3.5 w-3.5 text-info-fg" aria-hidden="true" />
              <span className="text-[0.65rem] font-medium uppercase tracking-wide text-fg-muted">
                Components
              </span>
            </div>
            <p className="mt-0.5 font-mono text-lg font-semibold text-fg">
              {data.componentCount}
            </p>
          </div>
        </div>

        {/* Detail lists */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {data.repositories.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold text-fg-secondary">
                Repositories Affected
              </h3>
              <ul className="space-y-0.5">
                {data.repositories.map((r) => (
                  <li key={r} className="truncate text-xs text-fg-muted">
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.components.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold text-fg-secondary">
                Components Affected
              </h3>
              <ul className="space-y-0.5">
                {data.components.map((c) => (
                  <li key={c} className="truncate text-xs text-fg-muted">
                    {c}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.dependencies.length > 0 && (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold text-fg-secondary">
                Dependency Impact
              </h3>
              <ul className="space-y-0.5">
                {data.dependencies.map((d) => (
                  <li key={d} className="truncate text-xs text-fg-muted">
                    {d}
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
