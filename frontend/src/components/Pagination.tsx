import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  page: number;
  pageSize: number;
  /** Rows matching the current filters across every page, not just this one. */
  total: number;
  onPageChange: (page: number) => void;
  /** Describes what's being counted, e.g. "repositories". */
  itemLabel?: string;
  /** Offered page sizes. Omit to hide the size selector entirely. */
  pageSizeOptions?: number[];
  onPageSizeChange?: (pageSize: number) => void;
}

/** Range + prev/next for any server-paginated list.
 *
 * Deliberately not a numbered page-picker: with a few thousand rows that
 * degenerates into either an unusable strip of numbers or an ellipsis
 * heuristic nobody navigates by. "Showing 25–48 of 1,204" plus stepping
 * and a page size is what actually gets used, and it stays the same
 * component whether the list holds 3 rows or 3,000. */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  itemLabel = "items",
  pageSizeOptions,
  onPageSizeChange,
}: PaginationProps) {
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-fg-muted">
      <p>
        {total === 0
          ? `No ${itemLabel}`
          : `Showing ${first.toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()} ${itemLabel}`}
      </p>
      <div className="flex items-center gap-3">
        {pageSizeOptions && onPageSizeChange && (
          <label className="flex items-center gap-1.5">
            <span>Per page</span>
            <select
              value={pageSize}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
              className="focus-ring rounded-md border border-line bg-surface px-1.5 py-1 text-xs text-fg-secondary"
            >
              {pageSizeOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            aria-label="Previous page"
            className="focus-ring rounded-md border border-line p-1 text-fg-secondary hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <span className="px-1 tabular-nums">
            {page} / {lastPage}
          </span>
          <button
            type="button"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= lastPage}
            aria-label="Next page"
            className="focus-ring rounded-md border border-line p-1 text-fg-secondary hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
