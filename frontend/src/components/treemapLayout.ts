/**
 * Squarified treemap layout — kept out of Treemap.tsx so these stay pure,
 * directly unit-testable, and don't break that file's fast-refresh
 * boundary (a component module should only export components; see
 * src/components/graph/graphLabels.ts for the same convention).
 */

export interface TreemapItem {
  id: string;
  label: string;
  /** The sizing metric — area is proportional to this, never to rank or
   * count of items. Zero/negative values are dropped before layout (a
   * zero-area rect can't be drawn or clicked meaningfully). */
  value: number;
  sublabel?: string;
  /** Omit for the categorical per-item palette (hashed from `id`, stable
   * across re-renders and re-sorts); set explicitly for a fixed semantic
   * color (e.g. a neutral grey "Ungrouped" bucket that must never be
   * mistaken for a real category). */
  color?: { background: string; text: string; border: string };
  /** True for a labeled-but-inert cell — the "Ungrouped" bucket is shown
   * for context (so its size relative to real domains is honest) but
   * isn't a drill-in target the same way a real domain is; its own repos
   * are already listed elsewhere on the page. */
  disabled?: boolean;
}

export interface TreemapRect {
  item: TreemapItem;
  x: number;
  y: number;
  width: number;
  height: number;
}

function worstAspectRatio(areas: number[], side: number): number {
  const sum = areas.reduce((a, b) => a + b, 0);
  const max = Math.max(...areas);
  const min = Math.min(...areas);
  if (sum === 0 || min === 0) return Infinity;
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/**
 * Squarified treemap layout (Bruls/Huizing/van Wijk) — rows are built
 * greedily, adding one item at a time only while doing so improves (or
 * doesn't worsen) the row's worst aspect ratio, which is what keeps cells
 * close to square instead of degenerating into slivers the "slice and
 * dice" algorithm produces for skewed value distributions (exactly what a
 * "one huge domain, several small ones" org looks like).
 */
export function computeTreemapLayout(
  items: TreemapItem[],
  x: number,
  y: number,
  width: number,
  height: number,
): TreemapRect[] {
  const positiveItems = items.filter((i) => i.value > 0);
  if (positiveItems.length === 0 || width <= 0 || height <= 0) return [];

  const totalValue = positiveItems.reduce((sum, i) => sum + i.value, 0);
  const totalArea = width * height;
  const areaById = new Map(positiveItems.map((i) => [i.id, (i.value / totalValue) * totalArea]));

  const result: TreemapRect[] = [];
  let remaining = [...positiveItems].sort((a, b) => b.value - a.value);
  let rx = x;
  let ry = y;
  let rw = width;
  let rh = height;

  while (remaining.length > 0) {
    const side = Math.min(rw, rh);
    let row = [remaining[0]];
    let i = 1;
    while (i < remaining.length) {
      const candidateRow = [...row, remaining[i]];
      const currentWorst = worstAspectRatio(row.map((r) => areaById.get(r.id) ?? 0), side);
      const candidateWorst = worstAspectRatio(candidateRow.map((r) => areaById.get(r.id) ?? 0), side);
      if (candidateWorst <= currentWorst) {
        row = candidateRow;
        i++;
      } else {
        break;
      }
    }

    const rowAreaSum = row.reduce((sum, r) => sum + (areaById.get(r.id) ?? 0), 0);
    const rowLength = side > 0 ? rowAreaSum / side : 0;

    if (rw >= rh) {
      let oy = ry;
      for (const item of row) {
        const itemHeight = rowLength > 0 ? (areaById.get(item.id) ?? 0) / rowLength : 0;
        result.push({ item, x: rx, y: oy, width: rowLength, height: itemHeight });
        oy += itemHeight;
      }
      rx += rowLength;
      rw -= rowLength;
    } else {
      let ox = rx;
      for (const item of row) {
        const itemWidth = rowLength > 0 ? (areaById.get(item.id) ?? 0) / rowLength : 0;
        result.push({ item, x: ox, y: ry, width: itemWidth, height: rowLength });
        ox += itemWidth;
      }
      ry += rowLength;
      rh -= rowLength;
    }

    remaining = remaining.slice(row.length);
  }

  return result;
}

// The categorical palette every other graph/legend in this app already
// uses (`src/components/graph/graphLabels.ts`) — reused here rather than
// inventing a second color system, so a domain's treemap color and (if it
// ever appears as a node elsewhere) its other renderings stay from the
// same visual family. Unlike that palette, this one *does* cycle (an org
// can have more than 8 domains; a repeated hue eight domains apart is a
// reasonable tradeoff a fixed 1:1 label mapping can't make).
const CATEGORY_SLOTS = 8;

function hashToSlot(id: string): number {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return (Math.abs(hash) % CATEGORY_SLOTS) + 1;
}

export function colorForItem(item: TreemapItem): { background: string; text: string; border: string } {
  if (item.color) return item.color;
  const slot = hashToSlot(item.id);
  return {
    background: `var(--gf-node-${slot}-bg)`,
    text: `var(--gf-node-${slot}-fg)`,
    border: `var(--gf-node-${slot}-line)`,
  };
}
