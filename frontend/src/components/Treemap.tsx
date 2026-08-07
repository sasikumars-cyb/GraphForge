import { useMemo } from "react";
import { computeTreemapLayout, colorForItem, type TreemapItem } from "./treemapLayout";

export type { TreemapItem } from "./treemapLayout";

const MIN_LABEL_WIDTH = 56;
const MIN_LABEL_HEIGHT = 32;

/**
 * A zoomable-in-spirit (click-to-drill, not a literal zoom animation)
 * treemap — area proportional to `value`, so relative size is a visual
 * read, not a number to compare across rows in a table. The Ownership
 * lens's own signature visualization (ARCHITECTURE_EXPERIENCE_REDESIGN.md):
 * "Team Payments' region is visibly the largest single block" needs to be
 * true at a glance, which an equal-sized card grid (what this replaced in
 * ArchitectureLanding) cannot do regardless of how the numbers are
 * formatted.
 */
export function Treemap({
  items,
  onSelect,
  height = 280,
  ariaLabel,
}: {
  items: TreemapItem[];
  onSelect?: (item: TreemapItem) => void;
  height?: number;
  ariaLabel: string;
}) {
  // ResizeObserver-based fluid width would be more correct, but every
  // other card on this page is already inside a fixed-width content
  // column (see AppLayout) — a 100%-wide SVG with a fixed viewBox tied to
  // a nominal width and `preserveAspectRatio="none"` stretches correctly
  // without needing to measure anything.
  const NOMINAL_WIDTH = 960;
  const rects = useMemo(
    () => computeTreemapLayout(items, 0, 0, NOMINAL_WIDTH, height),
    [items, height],
  );

  if (rects.length === 0) return null;

  return (
    <svg
      role="group"
      aria-label={ariaLabel}
      viewBox={`0 0 ${NOMINAL_WIDTH} ${height}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height }}
    >
      {rects.map(({ item, x, y, width, height: h }) => {
        const colors = colorForItem(item);
        const interactive = Boolean(onSelect) && !item.disabled;
        const showLabel = width >= MIN_LABEL_WIDTH && h >= MIN_LABEL_HEIGHT;
        const cellLabel = item.sublabel ? `${item.label}, ${item.sublabel}` : item.label;

        return (
          <g
            key={item.id}
            role={interactive ? "button" : undefined}
            tabIndex={interactive ? 0 : undefined}
            aria-label={interactive ? cellLabel : undefined}
            onClick={interactive ? () => onSelect?.(item) : undefined}
            onKeyDown={
              interactive
                ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect?.(item);
                    }
                  }
                : undefined
            }
            className={interactive ? "cursor-pointer outline-none" : undefined}
          >
            <rect
              x={x + 1}
              y={y + 1}
              width={Math.max(0, width - 2)}
              height={Math.max(0, h - 2)}
              fill={colors.background}
              stroke={colors.border}
              strokeWidth={1}
              rx={4}
              className={interactive ? "transition-opacity hover:opacity-80" : undefined}
            />
            {showLabel && (
              <text
                x={x + 8}
                y={y + 18}
                fill={colors.text}
                fontSize={12}
                fontWeight={600}
                className="pointer-events-none select-none"
              >
                {item.label.length > width / 7
                  ? `${item.label.slice(0, Math.floor(width / 7) - 1)}…`
                  : item.label}
              </text>
            )}
            {showLabel && item.sublabel && h >= MIN_LABEL_HEIGHT + 14 && (
              <text
                x={x + 8}
                y={y + 34}
                fill={colors.text}
                fontSize={10}
                opacity={0.8}
                className="pointer-events-none select-none"
              >
                {item.sublabel}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
