import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Table, type TableColumn } from "./Table";

interface Row {
  id: string;
  name: string;
}

const columns: TableColumn<Row>[] = [{ key: "name", header: "Name", render: (row) => row.name }];

describe("Table", () => {
  it("renders one row per data item", () => {
    const data: Row[] = [
      { id: "1", name: "order-service" },
      { id: "2", name: "payment-service" },
    ];

    render(<Table columns={columns} data={data} getRowKey={(row) => row.id} />);

    expect(screen.getByText("order-service")).toBeInTheDocument();
    expect(screen.getByText("payment-service")).toBeInTheDocument();
  });

  it("shows the empty message when there is no data", () => {
    render(
      <Table columns={columns} data={[]} getRowKey={(row) => row.id} emptyMessage="Nothing here" />,
    );

    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});
