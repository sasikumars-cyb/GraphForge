import type { ReactNode } from "react";

export interface TableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
}

interface TableProps<T> {
  columns: TableColumn<T>[];
  data: T[];
  getRowKey: (row: T) => string;
  emptyMessage?: string;
}

/** Generic, typed data table used by every list page. */
export function Table<T>({
  columns,
  data,
  getRowKey,
  emptyMessage = "No data to show.",
}: TableProps<T>) {
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
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`px-3 py-2 font-medium ${column.className ?? ""}`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line-muted">
          {data.map((row) => (
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
