import { ChevronRight } from "lucide-react";
import type { ArchitectureView } from "./types";

function Crumb({
  label,
  onClick,
  isCurrent,
}: {
  label: string;
  onClick?: () => void;
  isCurrent: boolean;
}) {
  if (isCurrent || !onClick) {
    return (
      <span
        className={isCurrent ? "font-medium text-fg" : "text-fg-muted"}
        aria-current={isCurrent ? "page" : undefined}
      >
        {label}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-fg-muted underline-offset-2 hover:text-fg-secondary hover:underline"
    >
      {label}
    </button>
  );
}

/** ADR "Architecture Page V2" — Org / Domain / Repository / Node, each
 * segment clickable to jump straight back to that level (not just "back
 * one step"). Only the segments relevant to the current view render —
 * a repository reached without going through a domain never shows a
 * domain crumb it was never navigated through. */
export function ArchitectureBreadcrumbs({
  view,
  onNavigate,
}: {
  view: ArchitectureView;
  onNavigate: (view: ArchitectureView) => void;
}) {
  const segments: { label: string; onClick?: () => void; isCurrent: boolean }[] = [
    {
      label: "Architecture",
      onClick: view.level !== "landing" ? () => onNavigate({ level: "landing" }) : undefined,
      isCurrent: view.level === "landing",
    },
  ];

  if (view.level === "domain" || view.level === "repository" || view.level === "neighborhood") {
    if (view.domain) {
      segments.push({
        label: view.domain,
        onClick:
          view.level !== "domain" ? () => onNavigate({ level: "domain", domain: view.domain! }) : undefined,
        isCurrent: view.level === "domain",
      });
    }
  }

  if (view.level === "repository" || view.level === "neighborhood") {
    segments.push({
      label: view.repositoryName,
      onClick:
        view.level !== "repository"
          ? () =>
              onNavigate({
                level: "repository",
                repositoryId: view.repositoryId,
                repositoryName: view.repositoryName,
                domain: view.domain,
              })
          : undefined,
      isCurrent: view.level === "repository",
    });
  }

  if (view.level === "neighborhood") {
    segments.push({ label: view.nodeLabel, isCurrent: true });
  }

  return (
    <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1.5 text-sm">
      {segments.map((segment, index) => (
        <span key={index} className="flex items-center gap-1.5">
          {index > 0 && <ChevronRight className="h-3.5 w-3.5 text-fg-subtle" aria-hidden="true" />}
          <Crumb label={segment.label} onClick={segment.onClick} isCurrent={segment.isCurrent} />
        </span>
      ))}
    </nav>
  );
}
