import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";

interface CollapsibleSectionProps {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/**
 * Reusable collapsible section with animated expand/collapse.
 * Used across all executive dashboard sections.
 */
export function CollapsibleSection({
  title,
  defaultOpen = true,
  children,
}: CollapsibleSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className="mb-4">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-lg px-1 py-2 text-left transition-colors hover:bg-surface-hover focus-ring"
        aria-expanded={isOpen}
      >
        <h2 className="font-display text-sm font-semibold tracking-tight text-fg">
          {title}
        </h2>
        <ChevronDown
          className={`h-4 w-4 text-fg-muted transition-transform duration-200 ${
            isOpen ? "rotate-0" : "-rotate-90"
          }`}
          aria-hidden="true"
        />
      </button>
      <div
        className={`overflow-hidden transition-all duration-200 ease-standard ${
          isOpen ? "max-h-[5000px] opacity-100" : "max-h-0 opacity-0"
        }`}
        aria-hidden={!isOpen}
      >
        <div className="pt-2">{children}</div>
      </div>
    </section>
  );
}
