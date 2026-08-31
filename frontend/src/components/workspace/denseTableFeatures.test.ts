import { constructTable, createColumnHelper, createPaginatedRowModel, tableFeatures } from "@tanstack/react-table";
import { storeReactivityBindings } from "@tanstack/table-core/store-reactivity-bindings";
import { describe, expect, it } from "vitest";

import { denseTableFeatures } from "./denseTableFeatures";

interface Row {
  id: string;
  name: string;
  score: number;
  secret: string;
}

const rows: Row[] = [
  { id: "alpha", name: "Alpha", score: 1, secret: "first-secret" },
  { id: "beta", name: "Beta", score: 2, secret: "second-secret" },
];

const testFeatures = tableFeatures({
  coreReactivityFeature: storeReactivityBindings(),
  ...denseTableFeatures,
  paginatedRowModel: createPaginatedRowModel(),
});
const columnHelper = createColumnHelper<typeof testFeatures, Row>();
const columns = columnHelper.columns([
  columnHelper.accessor((row) => row.name, { id: "name", filterFn: "includesString" }),
  columnHelper.accessor((row) => row.score, { id: "score" }),
  columnHelper.accessor((row) => row.secret, { id: "secret" }),
]);

describe("dense table state engine", () => {
  it("filters and sorts the rows supplied by the current data source", () => {
    const filtered = constructTable({
      features: testFeatures,
      columns,
      data: rows,
      state: { columnFilters: [{ id: "name", value: "beta" }] },
    });
    expect(filtered.getRowModel().rows.map((row) => row.original.id)).toEqual(["beta"]);

    const sorted = constructTable({
      features: testFeatures,
      columns,
      data: rows,
      state: { sorting: [{ id: "score", desc: true }] },
    });
    expect(sorted.getRowModel().rows.map((row) => row.original.id)).toEqual(["beta", "alpha"]);
  });

  it("tracks visibility and pagination without client-slicing a server page", () => {
    const table = constructTable({
      features: testFeatures,
      columns,
      data: rows,
      manualPagination: true,
      rowCount: 40,
      state: {
        columnVisibility: { secret: false },
        pagination: { pageIndex: 4, pageSize: 2 },
      },
    });
    expect(table.getVisibleLeafColumns().map((column) => column.id)).toEqual(["name", "score"]);
    expect(table.getRowModel().rows.map((row) => row.original.id)).toEqual(["alpha", "beta"]);
  });

  it("slices client data only when pagination is not manual", () => {
    const table = constructTable({
      features: testFeatures,
      columns,
      data: [...rows, { id: "gamma", name: "Gamma", score: 3, secret: "third-secret" }],
      state: { pagination: { pageIndex: 1, pageSize: 2 } },
    });
    expect(table.getRowModel().rows.map((row) => row.original.id)).toEqual(["gamma"]);
    expect(table.getPageCount()).toBe(2);
  });
});
