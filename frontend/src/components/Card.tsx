import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
}

/** Generic content container used across every page. */
export function Card({ title, description, action, children, className = "" }: CardProps) {
  const hasHeader = title !== undefined || description !== undefined || action !== undefined;

  return (
    <div className={`rounded-xl border border-line-muted bg-surface shadow-sm ${className}`}>
      {hasHeader && (
        <div className="flex items-start justify-between gap-4 border-b border-line-muted px-5 py-4">
          <div>
            {title && (
              <h2 className="font-display text-sm font-semibold tracking-tight text-fg">{title}</h2>
            )}
            {description && <p className="mt-0.5 text-xs text-fg-muted">{description}</p>}
          </div>
          {action}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
}
