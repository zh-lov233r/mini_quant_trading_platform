import { useMemo } from "react";

import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import { formatDateTime } from "@/utils/strategy";

import { DenseDataTable } from "./DenseDataTable";

export interface BacktestTransactionDenseRow {
  id: string;
  ts: string | null;
  side: string;
  symbol: string;
  qty: number;
  price: number;
  fee: number | null;
  netCashFlow: number | null;
  signalTs: string | null;
  stage: string | null;
  setupId: string | null;
  targetPct: number | null;
  addedNotional: number | null;
  weightedCost: number | null;
  reason: string;
}

export default function BacktestTransactionsDenseTable({ rows }: { rows: BacktestTransactionDenseRow[] }) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const columns = useMemo(() => [
    { id: "time", header: isZh ? "时间" : "Time", accessor: (row: BacktestTransactionDenseRow) => row.ts || "", cell: (value: unknown) => formatDateTime(String(value || ""), locale), sortable: true, width: 190 },
    { id: "side", header: isZh ? "方向" : "Side", accessor: (row: BacktestTransactionDenseRow) => row.side, cell: (value: unknown) => <Badge tone={value === "BUY" ? "success" : "warning"}>{String(value)}</Badge>, sortable: true, filterable: true, width: 100 },
    { id: "symbol", header: isZh ? "标的" : "Symbol", accessor: (row: BacktestTransactionDenseRow) => row.symbol, sortable: true, filterable: true, width: 110 },
    { id: "qty", header: isZh ? "成交股数" : "Shares Filled", accessor: (row: BacktestTransactionDenseRow) => row.qty, cell: (value: unknown) => `${Number(value).toLocaleString(locale, { maximumFractionDigits: 4 })}${isZh ? " 股" : " shares"}`, sortable: true, width: 150 },
    { id: "price", header: isZh ? "成交价" : "Price", accessor: (row: BacktestTransactionDenseRow) => row.price, cell: (value: unknown) => formatCurrency(Number(value), locale), sortable: true, width: 130 },
    { id: "fee", header: isZh ? "费用" : "Fee", accessor: (row: BacktestTransactionDenseRow) => row.fee, cell: (value: unknown) => formatCurrency(typeof value === "number" ? value : null, locale), sortable: true, width: 120 },
    { id: "cash", header: isZh ? "现金流" : "Cash Flow", accessor: (row: BacktestTransactionDenseRow) => row.netCashFlow, cell: (value: unknown) => formatCurrency(typeof value === "number" ? value : null, locale), sortable: true, width: 140 },
    { id: "signal", header: isZh ? "信号时间" : "Signal Time", accessor: (row: BacktestTransactionDenseRow) => row.signalTs || "", cell: (value: unknown) => formatDateTime(String(value || ""), locale), sortable: true, width: 190 },
    { id: "stage", header: isZh ? "阶段" : "Stage", accessor: (row: BacktestTransactionDenseRow) => row.stage || "-", filterable: true, width: 150 },
    { id: "target", header: isZh ? "累计目标" : "Cumulative Target", accessor: (row: BacktestTransactionDenseRow) => row.targetPct, cell: (value: unknown) => typeof value === "number" ? `${(value * 100).toFixed(0)}%` : "-", sortable: true, width: 145 },
    { id: "added", header: isZh ? "本批增持" : "Added Notional", accessor: (row: BacktestTransactionDenseRow) => row.addedNotional, cell: (value: unknown) => formatCurrency(typeof value === "number" ? value : null, locale), sortable: true, width: 150 },
    { id: "weighted", header: isZh ? "加权成本" : "Weighted Cost", accessor: (row: BacktestTransactionDenseRow) => row.weightedCost, cell: (value: unknown) => formatCurrency(typeof value === "number" ? value : null, locale), sortable: true, width: 145 },
    { id: "setup", header: "Setup", accessor: (row: BacktestTransactionDenseRow) => row.setupId || "-", filterable: true, width: 220 },
    { id: "reason", header: isZh ? "原因" : "Reason", accessor: (row: BacktestTransactionDenseRow) => row.reason || "-", filterable: true, width: 220 },
  ], [isZh, locale]);

  return <DenseDataTable columns={columns} rows={rows} getRowId={(row) => row.id} emptyText={isZh ? "这次 run 还没有写入任何交易记录" : "This run has not written any transaction records yet"} ariaLabel={isZh ? "交易明细" : "Transactions"} />;
}

function formatCurrency(value: number | null, locale: string): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat(locale, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
}
