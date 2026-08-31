import * as DropdownMenu from "radix-ui/dropdown-menu";
import {
  createColumnHelper,
  type ColumnFiltersState,
  type ColumnSizingState,
  type ColumnVisibilityState,
  type Header,
  type OnChangeFn,
  type PaginationState,
  type ReactTable,
  type RowData,
  type SortingState,
  useTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { useI18n } from "@/i18n/provider";
import { SelectControl } from "@/components/workspace/SelectControl";

import styles from "./DenseDataTable.module.css";
import { denseTableFeatures } from "./denseTableFeatures";
import { DEFAULT_VIRTUALIZE_ABOVE, resolveStableRowId, shouldVirtualizeRows } from "./workspaceLayout";

export interface DenseDataColumn<T extends RowData> {
  id: string;
  header: string;
  accessor: (row: T) => unknown;
  cell?: (value: unknown, row: T) => ReactNode;
  sortable?: boolean;
  filterable?: boolean;
  hideable?: boolean;
  width?: number;
}

export interface DenseDataTableProps<T extends RowData> {
  columns: DenseDataColumn<T>[];
  rows: T[];
  emptyText: string;
  getRowId: (row: T, index: number) => string;
  virtualizeAbove?: number;
  maxHeight?: number;
  ariaLabel?: string;
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  columnFilters?: ColumnFiltersState;
  onColumnFiltersChange?: OnChangeFn<ColumnFiltersState>;
  columnVisibility?: ColumnVisibilityState;
  onColumnVisibilityChange?: OnChangeFn<ColumnVisibilityState>;
  columnSizing?: ColumnSizingState;
  onColumnSizingChange?: OnChangeFn<ColumnSizingState>;
  pagination?: PaginationState;
  onPaginationChange?: OnChangeFn<PaginationState>;
  rowCount?: number;
  paginationMode?: "client" | "server";
  manualSorting?: boolean;
  manualFiltering?: boolean;
}

export function DenseDataTable<T extends RowData>({
  columns,
  rows,
  emptyText,
  getRowId,
  virtualizeAbove = DEFAULT_VIRTUALIZE_ABOVE,
  maxHeight = 520,
  ariaLabel,
  sorting: controlledSorting,
  onSortingChange,
  columnFilters: controlledColumnFilters,
  onColumnFiltersChange,
  columnVisibility: controlledColumnVisibility,
  onColumnVisibilityChange,
  columnSizing: controlledColumnSizing,
  onColumnSizingChange,
  pagination: controlledPagination,
  onPaginationChange,
  rowCount,
  paginationMode = "client",
  manualSorting = false,
  manualFiltering = false,
}: DenseDataTableProps<T>) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [internalSorting, setInternalSorting] = useState<SortingState>([]);
  const [internalColumnFilters, setInternalColumnFilters] = useState<ColumnFiltersState>([]);
  const [internalColumnVisibility, setInternalColumnVisibility] = useState<ColumnVisibilityState>({});
  const [internalColumnSizing, setInternalColumnSizing] = useState<ColumnSizingState>({});
  const [internalPagination, setInternalPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const parentRef = useRef<HTMLDivElement>(null);
  const sorting = controlledSorting ?? internalSorting;
  const columnFilters = controlledColumnFilters ?? internalColumnFilters;
  const columnVisibility = controlledColumnVisibility ?? internalColumnVisibility;
  const columnSizing = controlledColumnSizing ?? internalColumnSizing;
  const pagination = controlledPagination ?? internalPagination;
  const columnHelper = useMemo(() => createColumnHelper<typeof denseTableFeatures, T>(), []);
  const columnDefs = useMemo(() => columnHelper.columns(columns.map((column) => columnHelper.accessor(column.accessor, {
    id: column.id,
    header: column.header,
    enableSorting: column.sortable ?? false,
    enableColumnFilter: column.filterable ?? false,
    enableHiding: column.hideable ?? true,
    filterFn: column.filterable ? "includesString" : undefined,
    size: column.width || 160,
    cell: (context) => column.cell ? column.cell(context.getValue(), context.row.original) : String(context.getValue() ?? "-"),
  }))), [columnHelper, columns]);
  const table = useTable({
    features: denseTableFeatures,
    data: rows,
    columns: columnDefs,
    state: { sorting, columnFilters, columnVisibility, columnSizing, pagination },
    onSortingChange: onSortingChange ?? setInternalSorting,
    onColumnFiltersChange: onColumnFiltersChange ?? setInternalColumnFilters,
    onColumnVisibilityChange: onColumnVisibilityChange ?? setInternalColumnVisibility,
    onColumnSizingChange: onColumnSizingChange ?? setInternalColumnSizing,
    onPaginationChange: onPaginationChange ?? setInternalPagination,
    manualSorting,
    manualFiltering,
    manualPagination: paginationMode === "server",
    rowCount: rowCount ?? rows.length,
    columnResizeMode: "onChange",
    enableColumnResizing: true,
    getRowId: (row, index) => resolveStableRowId(row, index, getRowId),
  });
  const tableRows = table.getRowModel().rows;
  const virtualized = shouldVirtualizeRows(tableRows.length, virtualizeAbove);
  const virtualizer = useVirtualizer({ count: virtualized ? tableRows.length : 0, getScrollElement: () => parentRef.current, estimateSize: () => 38, overscan: 8 });
  const visibleHeaders = table.getFlatHeaders().filter((header) => header.column.getIsVisible());
  const gridTemplateColumns = visibleHeaders.map((header) => `${header.column.getSize()}px`).join(" ");

  if (!rows.length) return <div className={styles.wrapper}><div className={styles.empty}>{emptyText}</div></div>;

  return (
    <div className={styles.frame}>
      <div className={styles.toolbar}>
        <span>{table.getRowCount()} {isZh ? "行" : table.getRowCount() === 1 ? "row" : "rows"}</span>
        <div className={styles.toolbarActions}>
          <button type="button" className={styles.toolButton} onClick={() => { table.resetSorting(); table.resetColumnFilters(); }}>
            {isZh ? "重置" : "Reset"}
          </button>
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild><button type="button" className={styles.toolButton}>{isZh ? "列" : "Columns"}</button></DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content className={styles.columnMenu} align="end" sideOffset={6}>
                {table.getAllLeafColumns().filter((column) => column.getCanHide()).map((column) => (
                  <DropdownMenu.CheckboxItem
                    key={column.id}
                    className={styles.columnItem}
                    checked={column.getIsVisible()}
                    onCheckedChange={(checked) => column.toggleVisibility(Boolean(checked))}
                    onSelect={(event) => event.preventDefault()}
                  >
                    <DropdownMenu.ItemIndicator className={styles.check}>✓</DropdownMenu.ItemIndicator>
                    {String(column.columnDef.header || column.id)}
                  </DropdownMenu.CheckboxItem>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </div>
      <div ref={parentRef} className={styles.wrapper} style={{ maxHeight }} role={virtualized ? "table" : undefined} aria-label={ariaLabel} aria-rowcount={virtualized ? table.getRowCount() : undefined} data-virtualized={virtualized ? "true" : "false"}>
      {!virtualized ? (
        <table className={styles.table} aria-label={ariaLabel} style={{ minWidth: table.getTotalSize() }}>
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id} className={styles.headerCell} style={{ width: header.column.getSize() }} aria-sort={sortLabel(header.column.getIsSorted())}>{header.isPlaceholder ? null : <DenseHeader table={table} header={header} isZh={isZh} />}{header.column.getCanResize() ? <span role="separator" aria-orientation="vertical" className={`${styles.resizer} ${header.column.getIsResizing() ? styles.resizing : ""}`} onMouseDown={header.getResizeHandler()} onTouchStart={header.getResizeHandler()} /> : null}</th>)}</tr>)}</thead>
          <tbody>{tableRows.map((row) => <tr key={row.id} className={styles.row}>{row.getVisibleCells().map((cell) => <td key={cell.id} className={styles.cell} style={{ width: cell.column.getSize() }}><table.FlexRender cell={cell} /></td>)}</tr>)}</tbody>
        </table>
      ) : (
        <>
          <div role="rowgroup" style={{ display: "grid", gridTemplateColumns }}>{visibleHeaders.map((header) => <div role="columnheader" key={header.id} className={styles.headerCell} aria-sort={sortLabel(header.column.getIsSorted())}>{header.isPlaceholder ? null : <DenseHeader table={table} header={header} isZh={isZh} />}{header.column.getCanResize() ? <span role="separator" aria-orientation="vertical" className={`${styles.resizer} ${header.column.getIsResizing() ? styles.resizing : ""}`} onMouseDown={header.getResizeHandler()} onTouchStart={header.getResizeHandler()} /> : null}</div>)}</div>
          <div className={styles.virtualViewport} style={{ height: virtualizer.getTotalSize(), minWidth: table.getTotalSize() }} role="rowgroup">
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = tableRows[virtualRow.index];
              return <div key={row.id} className={styles.virtualRow} role="row" style={{ gridTemplateColumns, transform: `translateY(${virtualRow.start}px)` }}>{row.getVisibleCells().map((cell) => <div key={cell.id} role="cell" className={styles.virtualCell}><table.FlexRender cell={cell} /></div>)}</div>;
            })}
          </div>
        </>
      )}
      </div>
      <div className={styles.pagination}>
        <label>{isZh ? "每页" : "Rows"} <SelectControl density="compact" aria-label={isZh ? "每页行数" : "Rows per page"} value={pagination.pageSize} onValueChange={(value) => table.setPageSize(Number(value))} options={[25, 50, 100, 200].map((size) => ({ value: size, label: size }))} /></label>
        <span>{isZh ? "第" : "Page"} {pagination.pageIndex + 1} / {Math.max(1, table.getPageCount())}{isZh ? "页" : ""}</span>
        <button type="button" className={styles.toolButton} onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>{isZh ? "上一页" : "Previous"}</button>
        <button type="button" className={styles.toolButton} onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>{isZh ? "下一页" : "Next"}</button>
      </div>
    </div>
  );
}

function DenseHeader<T extends RowData>({
  table,
  header,
  isZh,
}: {
  table: ReactTable<typeof denseTableFeatures, T>;
  header: Header<typeof denseTableFeatures, T, unknown>;
  isZh: boolean;
}) {
  const canSort = header.column.getCanSort();
  const canFilter = header.column.getCanFilter();
  return (
    <div className={styles.headerContent}>
      {canSort ? (
        <button type="button" className={styles.sortButton} onClick={header.column.getToggleSortingHandler()}>
          <table.FlexRender header={header} />{sortGlyph(header.column.getIsSorted())}
        </button>
      ) : <span><table.FlexRender header={header} /></span>}
      {canFilter ? (
        <input
          className={styles.filterInput}
          value={String(header.column.getFilterValue() ?? "")}
          onChange={(event) => header.column.setFilterValue(event.target.value)}
          aria-label={`${String(header.column.columnDef.header || header.column.id)}${isZh ? "筛选" : " filter"}`}
        />
      ) : null}
    </div>
  );
}

function sortLabel(value: false | "asc" | "desc"): "none" | "ascending" | "descending" {
  return value === "asc" ? "ascending" : value === "desc" ? "descending" : "none";
}

function sortGlyph(value: false | "asc" | "desc"): string {
  return value === "asc" ? " ↑" : value === "desc" ? " ↓" : "";
}
