import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
  /**
   * Makes this column sortable. Returns the value to compare — a string,
   * number, or `null` for "put this row last regardless of direction"
   * (e.g. a run with no `completed_at` yet). Omit entirely for a column
   * that shouldn't be sortable (actions, avatars, free-text summaries where
   * no ordering is meaningful) — those render exactly as before.
   */
  sortValue?: (row: T) => string | number | null;
}

interface TableProps<T> {
  columns: TableColumn<T>[];
  data: T[];
  getRowKey: (row: T) => string;
  emptyMessage?: string;
  /** Column key sorted by default, and its direction. Omit for insertion
   * order (most tables here are already sorted server-side, e.g. Run
   * History's recency order — this doesn't fight that on first render). */
  defaultSort?: { key: string; direction: "asc" | "desc" };
}

type SortDirection = "asc" | "desc";

/** Generic, typed data table used by every list page.
 *
 * Sorting is client-side and opt-in per column via `sortValue` — every list
 * page here (Runs, Repositories, PRs, Model Usage, Recent Workflows) was
 * fixed in whatever order the API returned until now, which is fine for
 * "most recent first" but leaves no way to ask "which run cost the most" or
 * "which repository has the fewest components" without reading every row. */
export function Table<T>({
  columns,
  data,
  getRowKey,
  emptyMessage = "No data to show.",
  defaultSort,
}: TableProps<T>) {
  const [sort, setSort] = useState<{ key: string; direction: SortDirection } | null>(
    defaultSort ?? null,
  );

  const sortedData = useMemo(() => {
    if (!sort) return data;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.sortValue) return data;
    // Decorate-sort-undecorate with the original index as a stable-sort
    // tiebreaker — Array.prototype.sort's stability varies enough across
    // engines/versions that relying on it implicitly for equal keys isn't
    // worth it when preserving row order for ties is one line to guarantee.
    const decorated = data.map((row, index) => ({
      row,
      index,
      value: column.sortValue!(row),
    }));
    decorated.sort((a, b) => {
      // Nulls sort last regardless of direction — a "no cost recorded yet"
      // row belongs at the bottom whether the reader asked for cheapest- or
      // most-expensive-first, not wherever `null < value` happens to place it.
      if (a.value === null && b.value === null) return a.index - b.index;
      if (a.value === null) return 1;
      if (b.value === null) return -1;
      const cmp = a.value < b.value ? -1 : a.value > b.value ? 1 : 0;
      if (cmp !== 0) return sort.direction === "asc" ? cmp : -cmp;
      return a.index - b.index;
    });
    return decorated.map((d) => d.row);
  }, [data, columns, sort]);

  function toggleSort(key: string) {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: "asc" };
      if (current.direction === "asc") return { key, direction: "desc" };
      return null; // third click clears back to the original order
    });
  }

  if (data.length === 0) {
    return <p className="py-8 text-center text-sm text-fg-muted">{emptyMessage}</p>;
  }

  return (
    <div className="overflow-x-auto">
      {/* `min-w-max` used to sit alongside `w-full` here, which always wins
          the width/min-width conflict — the table was forced to its natural
          (never-wrap) content width even when that was narrower than the
          card, forcing a horizontal scrollbar for cards with room to spare
          (e.g. Approved Queue's disabled "Coming soon" action cell). Plain
          `w-full` lets it size to the container first; `overflow-x-auto`
          above still scrolls it on genuinely narrow viewports. */}
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wide text-fg-muted">
            {columns.map((column) => {
              const isSortable = Boolean(column.sortValue);
              const isActive = sort?.key === column.key;
              const ariaSort = !isSortable
                ? undefined
                : isActive
                  ? sort!.direction === "asc"
                    ? "ascending"
                    : "descending"
                  : "none";
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={ariaSort}
                  className={`px-3 py-2 font-medium ${column.className ?? ""}`}
                >
                  {isSortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column.key)}
                      className="focus-ring inline-flex items-center gap-1 rounded hover:text-fg-secondary"
                    >
                      {column.header}
                      {isActive ? (
                        sort!.direction === "asc" ? (
                          <ArrowUp className="h-3 w-3" aria-hidden="true" />
                        ) : (
                          <ArrowDown className="h-3 w-3" aria-hidden="true" />
                        )
                      ) : (
                        <ChevronsUpDown
                          className="h-3 w-3 opacity-40"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-muted">
          {sortedData.map((row) => (
            <tr
              key={getRowKey(row)}
              className="text-fg-secondary transition-colors hover:bg-surface-hover"
            >
              {columns.map((column) => (
                <td key={column.key} className={`px-3 py-3 ${column.className ?? ""}`}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
