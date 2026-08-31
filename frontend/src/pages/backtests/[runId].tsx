import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/router";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getBacktestEquity,
  getBacktestSignals,
  getBacktestSummary,
  getBacktestSupportResistance,
  getBacktestTransactions,
  getBacktestWorkerStatus,
} from "@/api/backtests";
import { getCandleSeries } from "@/api/quotes";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import BacktestProgressBar from "@/components/BacktestProgressBar";
import BacktestPerformanceProbe from "@/components/charts/BacktestPerformanceProbe";
import MetricCard from "@/components/MetricCard";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogNote as ContextNote, DialogStack as ContextStack, DialogStat as ContextStat, DialogStats as ContextStats, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import type { BacktestTransactionDenseRow } from "@/components/workspace/BacktestTransactionsDenseTable";
import { useI18n } from "@/i18n/provider";
import type {
  BacktestComparisonCurvePoint,
  BacktestDetailOut,
  BacktestEquityPoint,
  BacktestSnapshotPoint,
  BacktestSignalOut,
  BacktestTransactionOut,
  BacktestWorkerStatus,
  SupportResistanceBacktestOut,
  SupportResistanceRunEventOut,
} from "@/types/backtest";
import type { CandleBarOut, CandleSeriesOut } from "@/types/quote";
import { formatDateTime, formatDurationMs, formatPercent } from "@/utils/strategy";
import { compactBacktestPositions } from "@/utils/backtestPositions";
import { shouldLoadBacktestDetails } from "@/utils/backtestProgress";
import {
  BacktestTransactionPageError,
  INITIAL_LIFECYCLE_TRANSACTION_LIMIT,
  INITIAL_TRANSACTION_PAGE_SIZE,
  LIFECYCLE_ROW_BATCH_SIZE,
  LIFECYCLE_TRANSACTION_BATCH_SIZE,
  mergeBacktestTransactions,
  nextVisibleItemCount,
  streamBacktestTransactionPages,
  TRANSACTION_TABLE_BATCH_SIZE,
  TRANSACTION_TAIL_PAGE_SIZE,
} from "@/utils/backtestTransactions";
import {
  buildEquityEventMarkers,
  currentZoneOverlays,
  isDisplayableSupportResistanceEventType,
  normalizeEquityPoints,
  toChartTime,
} from "@/components/charts/chartModels";
import type {
  ChartGapOverlay,
  ChartOverlayMarker,
  ChartZoneOverlay,
} from "@/components/charts/chartModels";

// Keep the chart engine out of the shared application and server-rendered bundles.
const EquityLightweightChart = dynamic(
  () => import("@/components/charts/EquityLightweightChart"),
  { ssr: false },
);
const CandlestickLightweightChart = dynamic(
  () => import("@/components/charts/CandlestickLightweightChart"),
  { ssr: false },
);
const BacktestTransactionsDenseTable = dynamic(
  () => import("@/components/workspace/BacktestTransactionsDenseTable"),
  { ssr: false }
);

function actionLink(href: string, label: string, filled = false) {
  return (
    <Link
      href={href}
      style={{
        padding: "11px 16px",
        borderRadius: 14,
        border: filled ? "none" : "1px solid rgba(148, 163, 184, 0.16)",
        background: filled ? "#0891b2" : "rgba(15, 23, 42, 0.72)",
        color: filled ? "#f8fafc" : "#dbeafe",
        textDecoration: "none",
        fontWeight: 700,
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
      }}
    >
      {label}
    </Link>
  );
}

function metricNumber(summary: Record<string, unknown>, key: string): number | null {
  const value = summary[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

const BACKTEST_DETAIL_LOAD_FAILED = "__BACKTEST_DETAIL_LOAD_FAILED__";
const BACKTEST_EQUITY_LOAD_FAILED = "__BACKTEST_EQUITY_LOAD_FAILED__";
const BACKTEST_SIGNALS_LOAD_FAILED = "__BACKTEST_SIGNALS_LOAD_FAILED__";
const BACKTEST_TRANSACTIONS_LOAD_FAILED = "__BACKTEST_TRANSACTIONS_LOAD_FAILED__";

interface TransactionCollectionState {
  total: number;
  nextCursor: string | null;
  loading: boolean;
  complete: boolean;
  error: string | null;
}

const EMPTY_TRANSACTION_COLLECTION: TransactionCollectionState = {
  total: 0,
  nextCursor: null,
  loading: false,
  complete: false,
  error: null,
};

function detailErrorMessage(error: string, isZh: boolean): string {
  if (error === BACKTEST_EQUITY_LOAD_FAILED) {
    return isZh ? "加载权益曲线失败" : "Failed to load equity curve";
  }
  if (error === BACKTEST_SIGNALS_LOAD_FAILED) {
    return isZh ? "加载信号失败" : "Failed to load signals";
  }
  if (error === BACKTEST_TRANSACTIONS_LOAD_FAILED) {
    return isZh ? "加载交易明细失败" : "Failed to load transactions";
  }
  return error;
}

function formatCurrency(value?: number | null, locale = "en-US"): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return value.toLocaleString(locale, { maximumFractionDigits: 2 });
}

function getMetaText(meta: Record<string, unknown> | undefined, key: string): string | null {
  const value = meta?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getMetaNumber(meta: Record<string, unknown> | undefined, key: string): number | null {
  const value = meta?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getObjectValue(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function getRecordText(record: Record<string, unknown> | null, key: string): string | null {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function getRecordNumber(record: Record<string, unknown> | null, key: string): number | null {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function buildLinePath(
  values: number[],
  width: number,
  height: number,
  paddingX: number,
  paddingTop: number,
  paddingBottom: number,
  minValue?: number,
  maxValue?: number
): string {
  if (values.length === 0) {
    return "";
  }

  const min = typeof minValue === "number" ? minValue : Math.min(...values);
  const max = typeof maxValue === "number" ? maxValue : Math.max(...values);
  const xStep = values.length > 1 ? (width - paddingX * 2) / (values.length - 1) : 0;
  const yRange = max - min || 1;
  const usableHeight = height - paddingTop - paddingBottom;

  return values
    .map((value, index) => {
      const x = paddingX + index * xStep;
      const y = height - paddingBottom - ((value - min) / yRange) * usableHeight;
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function buildAreaPath(
  values: number[],
  width: number,
  height: number,
  paddingX: number,
  paddingTop: number,
  paddingBottom: number,
  minValue?: number,
  maxValue?: number
): string {
  if (values.length === 0) {
    return "";
  }

  const linePath = buildLinePath(
    values,
    width,
    height,
    paddingX,
    paddingTop,
    paddingBottom,
    minValue,
    maxValue
  );
  const xStep = values.length > 1 ? (width - paddingX * 2) / (values.length - 1) : 0;
  const lastX = paddingX + (values.length - 1) * xStep;
  const baseY = height - paddingBottom;
  return `${linePath} L ${lastX.toFixed(2)} ${baseY.toFixed(2)} L ${paddingX.toFixed(
    2
  )} ${baseY.toFixed(2)} Z`;
}

function projectValueToChartY(
  value: number,
  height: number,
  paddingTop: number,
  paddingBottom: number,
  minValue: number,
  maxValue: number
): number {
  const yRange = maxValue - minValue || 1;
  const usableHeight = height - paddingTop - paddingBottom;
  return height - paddingBottom - ((value - minValue) / yRange) * usableHeight;
}

function buildYAxisTicks(
  min: number,
  max: number,
  height: number,
  paddingTop: number,
  paddingBottom: number,
  count = 5
) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || count < 2) {
    return [];
  }

  let visibleMin = min;
  let visibleMax = max;
  if (visibleMin === visibleMax) {
    const delta = Math.max(Math.abs(visibleMin) * 0.02, 1);
    visibleMin -= delta;
    visibleMax += delta;
  }

  const range = visibleMax - visibleMin || 1;
  const usableHeight = height - paddingTop - paddingBottom;
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / (count - 1);
    const value = visibleMax - ratio * range;
    const y = paddingTop + ratio * usableHeight;
    return { value, y };
  });
}

type ChartMarker = {
  key: string;
  x: number;
  y: number;
  category: "buy_signal" | "sell_signal" | "buy_fill" | "sell_fill";
  shape: "circle" | "triangle-up" | "triangle-down";
  stroke: string;
  fill: string;
  title: string;
  details: string[];
};

type MarkerVisibility = Record<ChartMarker["category"], boolean>;

type SymbolPnlRow = {
  symbol: string;
  realizedPnl: number;
  unrealizedPnl: number;
  totalPnl: number;
  netQty: number;
  marketValue: number;
  lastPrice: number | null;
  tradeCount: number;
};

type PositionLifecycleRow = {
  key: string;
  symbol: string;
  sequence: number;
  status: "closed" | "open";
  qty: number;
  entryTs: string | null;
  entrySignalTs: string | null;
  exitTs: string | null;
  exitSignalTs: string | null;
  markTs: string | null;
  entryTradeDate: string | null;
  entrySignalTradeDate: string | null;
  exitTradeDate: string | null;
  exitSignalTradeDate: string | null;
  markTradeDate: string | null;
  entryPrice: number;
  exitPrice: number | null;
  markPrice: number | null;
  pnl: number | null;
  returnPct: number | null;
  holdingDays: number | null;
  entryReason: string | null;
  exitReason: string | null;
};

type PositionLifecyclePnlFilter = "all" | "profit" | "loss" | "flat";

type PositionLifecycleSortKey =
  | "symbol"
  | "entryTime"
  | "endTime"
  | "qty"
  | "entryPrice"
  | "endPrice"
  | "pnl"
  | "returnPct"
  | "holdingDays";

type PositionLifecycleSortDirection = "asc" | "desc";

type LifecycleChartMarker = {
  key: string;
  label: string;
  date: string;
  price: number | null;
  tone:
    | "buy"
    | "buy_signal"
    | "sell"
    | "sell_signal"
    | "mark"
    | "neckline"
    | "breakout"
    | "left_bottom"
    | "right_bottom";
  description: string;
};

type SupportResistanceSignalSetup = {
  zoneKey: string;
  selectedSetup: string;
  lower: number;
  upper: number;
  validFrom: string | null;
  score: number | null;
  resolvedSamples: number | null;
};

type LifecycleZoneOverlay = {
  key: string;
  startDate: string;
  endDate: string;
  startCenterPrice: number;
  startLowerPrice: number;
  startUpperPrice: number;
  endCenterPrice: number;
  endLowerPrice: number;
  endUpperPrice: number;
  slopePerSession: number;
  slopeAtrPerSession: number | null;
  role: "support" | "resistance";
  description: string;
};

type IslandReversalGapSetup = {
  leftGapTradeDate: string;
  breakoutTradeDate: string;
  islandHigh: number;
  breakoutGapLow: number;
  leftGapPct: number | null;
  breakoutGapPct: number | null;
};

type DoubleBottomSetup = {
  leftBottomTradeDate: string;
  rightBottomTradeDate: string;
  leftBottomLow: number;
  rightBottomLow: number;
  necklineTradeDate: string | null;
  necklinePrice: number | null;
  breakoutTradeDate: string | null;
  breakoutClose: number | null;
  breakoutWaitBars: number | null;
  breakoutVolumeRatio: number | null;
  bottomDistancePct: number | null;
};

type LifecycleGapOverlay = {
  key: string;
  label: string;
  referenceDate: string;
  anchorDate: string;
  lowPrice: number;
  highPrice: number;
  tone: "left_gap" | "right_gap";
  description: string;
};

type OpenLifecycleLot = {
  symbol: string;
  qty: number;
  entryTs: string | null;
  entrySignalTs: string | null;
  entryTradeDate: string | null;
  entrySignalTradeDate: string | null;
  entryPrice: number;
  entryFee: number;
  entryReason: string | null;
  txId: string;
};

type CurveVisibility = {
  strategy: boolean;
  SPY: boolean;
  QQQ: boolean;
};

const LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS = 30;
const LIFECYCLE_POST_EXIT_LOOKAHEAD_DAYS = 0;
const LIFECYCLE_PRE_ENTRY_FETCH_BUFFER_DAYS = 90;
const LIFECYCLE_PRE_ENTRY_LOOKBACK_MIN = 0;
const LIFECYCLE_PRE_ENTRY_LOOKBACK_MAX = 240;
const LIFECYCLE_PRE_ENTRY_LOOKBACK_PRESETS = [10, 30, 60, 120];
const LIFECYCLE_POST_EXIT_LOOKAHEAD_PRESETS = [0, 10, 30, 60];

function toTimeValue(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}

function toTradeDateKey(value?: string | null): string | null {
  if (!value) {
    return null;
  }
  if (value.includes("T")) {
    const [datePart] = value.split("T");
    return datePart || null;
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(value)) {
    return value.slice(0, 10);
  }
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    return null;
  }
  return parsed.toISOString().slice(0, 10);
}

function formatTradeDate(value?: string | null, locale = "en-US"): string {
  const tradeDate = toTradeDateKey(value);
  if (!tradeDate) {
    return "-";
  }

  const [year, month, day] = tradeDate.split("-").map((part) => Number(part));
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (Number.isNaN(parsed.getTime())) {
    return tradeDate;
  }

  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "UTC",
  }).format(parsed);
}

function formatLifecycleEventMoment(
  ts: string | null | undefined,
  tradeDate: string | null | undefined,
  locale = "en-US"
): string {
  if (tradeDate) {
    return formatTradeDate(tradeDate, locale);
  }
  return formatDateTime(ts, locale);
}

function normalizePositionPayload(value: unknown): { qty: number; close: number; marketValue: number } | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const qty = typeof payload.qty === "number" ? payload.qty : null;
  const close = typeof payload.close === "number" ? payload.close : null;
  const marketValue = typeof payload.market_value === "number" ? payload.market_value : null;
  if (qty == null || close == null || marketValue == null) {
    return null;
  }
  return { qty, close, marketValue };
}

function sortTransactionsChronologically(transactions: BacktestTransactionOut[]) {
  return [...transactions].sort((left, right) => {
    const leftTs = toTimeValue(left.ts) || 0;
    const rightTs = toTimeValue(right.ts) || 0;
    if (leftTs !== rightTs) {
      return leftTs - rightTs;
    }
    return left.id.localeCompare(right.id);
  });
}

function holdingDaysBetween(startTradeDate: string | null, endTradeDate: string | null): number | null {
  if (!startTradeDate || !endTradeDate) {
    return null;
  }

  const start = Date.parse(`${startTradeDate}T00:00:00Z`);
  const end = Date.parse(`${endTradeDate}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return null;
  }
  return Math.max(0, Math.round((end - start) / 86_400_000));
}

function shiftDateKey(dateKey: string | null, deltaDays: number): string | null {
  if (!dateKey) {
    return null;
  }

  const base = new Date(`${dateKey}T00:00:00Z`);
  if (!Number.isFinite(base.getTime())) {
    return null;
  }

  base.setUTCDate(base.getUTCDate() + deltaDays);
  return base.toISOString().slice(0, 10);
}

function trimLifecycleBars(
  bars: CandleBarOut[],
  entryTradeDate: string | null,
  endTradeDate: string | null,
  lookbackTradingDays: number,
  lookaheadTradingDays = 0
) {
  if (!entryTradeDate || !endTradeDate || bars.length === 0) {
    return bars;
  }

  const entryIndex = bars.findIndex((bar) => bar.trade_date >= entryTradeDate);
  let exitIndex = -1;
  for (let index = bars.length - 1; index >= 0; index -= 1) {
    if (bars[index].trade_date <= endTradeDate) {
      exitIndex = index;
      break;
    }
  }

  if (entryIndex < 0 || exitIndex < 0 || exitIndex < entryIndex) {
    return bars;
  }

  const startIndex = Math.max(0, entryIndex - lookbackTradingDays);
  const finalEndIndex = Math.min(
    bars.length - 1,
    exitIndex + Math.max(0, lookaheadTradingDays)
  );
  return bars.slice(startIndex, finalEndIndex + 1);
}

function normalizeLifecycleLookbackDays(value: number) {
  if (!Number.isFinite(value)) {
    return LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS;
  }
  return Math.round(
    Math.min(
      LIFECYCLE_PRE_ENTRY_LOOKBACK_MAX,
      Math.max(LIFECYCLE_PRE_ENTRY_LOOKBACK_MIN, value)
    )
  );
}

function estimateLifecycleFetchBufferDays(lookbackTradingDays: number) {
  const estimatedCalendarDays = Math.ceil(lookbackTradingDays * 1.8) + 20;
  return Math.max(LIFECYCLE_PRE_ENTRY_FETCH_BUFFER_DAYS, estimatedCalendarDays);
}

function buildPositionLifecycleRows(run: BacktestDetailOut): PositionLifecycleRow[] {
  const orderedTransactions = sortTransactionsChronologically(run.transactions);
  const latestSnapshotTs = run.latest_snapshot?.ts || run.finished_at || run.window_end || null;
  const latestSnapshotTradeDate = toTradeDateKey(latestSnapshotTs);
  const latestPositions = run.latest_snapshot?.positions || {};
  const openLotsBySymbol = new Map<string, OpenLifecycleLot[]>();
  const cycleCountBySymbol = new Map<string, number>();
  const rows: PositionLifecycleRow[] = [];

  orderedTransactions.forEach((txn) => {
    const symbol = txn.symbol.toUpperCase();
    const fee = typeof txn.fee === "number" ? txn.fee : 0;
    const tradeDate = getMetaText(txn.meta, "execution_trade_date") || toTradeDateKey(txn.ts);
    const signalTs = getMetaText(txn.meta, "signal_ts");
    const signalTradeDate = toTradeDateKey(signalTs);
    const reason = getMetaText(txn.meta, "reason");

    if (txn.side === "BUY") {
      const lots = openLotsBySymbol.get(symbol) || [];
      lots.push({
        symbol,
        qty: txn.qty,
        entryTs: txn.ts || null,
        entrySignalTs: signalTs,
        entryTradeDate: tradeDate,
        entrySignalTradeDate: signalTradeDate,
        entryPrice: txn.price,
        entryFee: fee,
        entryReason: reason,
        txId: txn.id,
      });
      openLotsBySymbol.set(symbol, lots);
      return;
    }

    if (txn.side !== "SELL") {
      return;
    }

    let remainingQty = txn.qty;
    const lots = openLotsBySymbol.get(symbol) || [];

    while (remainingQty > 1e-9 && lots.length > 0) {
      const lot = lots[0];
      const lotQtyBeforeMatch = lot.qty;
      const matchedQty = Math.min(remainingQty, lotQtyBeforeMatch);
      const entryFeeAllocated =
        lotQtyBeforeMatch > 0 ? lot.entryFee * (matchedQty / lotQtyBeforeMatch) : 0;
      const exitFeeAllocated = txn.qty > 0 ? fee * (matchedQty / txn.qty) : 0;
      const entryCost = matchedQty * lot.entryPrice + entryFeeAllocated;
      const exitProceeds = matchedQty * txn.price - exitFeeAllocated;
      const pnl = exitProceeds - entryCost;
      const sequence = (cycleCountBySymbol.get(symbol) || 0) + 1;
      cycleCountBySymbol.set(symbol, sequence);

      rows.push({
        key: `${symbol}-${sequence}-${lot.txId}-${txn.id}`,
        symbol,
        sequence,
        status: "closed",
        qty: matchedQty,
        entryTs: lot.entryTs,
        entrySignalTs: lot.entrySignalTs,
        exitTs: txn.ts || null,
        exitSignalTs: signalTs,
        markTs: null,
        entryTradeDate: lot.entryTradeDate,
        entrySignalTradeDate: lot.entrySignalTradeDate,
        exitTradeDate: tradeDate,
        exitSignalTradeDate: signalTradeDate,
        markTradeDate: null,
        entryPrice: lot.entryPrice,
        exitPrice: txn.price,
        markPrice: null,
        pnl,
        returnPct: entryCost > 0 ? pnl / entryCost : null,
        holdingDays: holdingDaysBetween(lot.entryTradeDate, tradeDate),
        entryReason: lot.entryReason,
        exitReason: reason,
      });

      lot.qty = Math.max(lot.qty - matchedQty, 0);
      lot.entryFee = Math.max(lot.entryFee - entryFeeAllocated, 0);
      remainingQty = Math.max(remainingQty - matchedQty, 0);

      if (lot.qty <= 1e-9) {
        lots.shift();
      } else {
        lots[0] = lot;
      }
    }

    if (lots.length > 0) {
      openLotsBySymbol.set(symbol, lots);
    } else {
      openLotsBySymbol.delete(symbol);
    }
  });

  openLotsBySymbol.forEach((lots, symbol) => {
    const positionPayload = normalizePositionPayload(latestPositions[symbol]);

    lots.forEach((lot) => {
      const sequence = (cycleCountBySymbol.get(symbol) || 0) + 1;
      cycleCountBySymbol.set(symbol, sequence);
      const markPrice = positionPayload?.close ?? null;
      const entryCost = lot.qty * lot.entryPrice + lot.entryFee;
      const markValue = markPrice != null ? lot.qty * markPrice : null;
      const pnl = markValue != null ? markValue - entryCost : null;

      rows.push({
        key: `${symbol}-${sequence}-${lot.txId}-open`,
        symbol,
        sequence,
        status: "open",
        qty: lot.qty,
        entryTs: lot.entryTs,
        entrySignalTs: lot.entrySignalTs,
        exitTs: null,
        exitSignalTs: null,
        markTs: latestSnapshotTs,
        entryTradeDate: lot.entryTradeDate,
        entrySignalTradeDate: lot.entrySignalTradeDate,
        exitTradeDate: null,
        exitSignalTradeDate: null,
        markTradeDate: latestSnapshotTradeDate,
        entryPrice: lot.entryPrice,
        exitPrice: null,
        markPrice,
        pnl,
        returnPct: pnl != null && entryCost > 0 ? pnl / entryCost : null,
        holdingDays: holdingDaysBetween(lot.entryTradeDate, latestSnapshotTradeDate),
        entryReason: lot.entryReason,
        exitReason: null,
      });
    });
  });

  return rows.sort((left, right) => {
    const rightTime = toTimeValue(right.exitTs || right.markTs || right.entryTs) || 0;
    const leftTime = toTimeValue(left.exitTs || left.markTs || left.entryTs) || 0;
    if (rightTime !== leftTime) {
      return rightTime - leftTime;
    }
    return left.symbol.localeCompare(right.symbol);
  });
}

function getLifecycleDisplayEndTs(row: PositionLifecycleRow): string | null {
  return row.exitTs || row.markTs || null;
}

function getLifecycleDisplayEndPrice(row: PositionLifecycleRow): number | null {
  return row.exitPrice ?? row.markPrice;
}

function getLifecycleSortFallbackTime(row: PositionLifecycleRow): number | null {
  return toTimeValue(getLifecycleDisplayEndTs(row) || row.entryTs);
}

function defaultLifecycleSortDirection(
  key: PositionLifecycleSortKey
): PositionLifecycleSortDirection {
  return key === "symbol" ? "asc" : "desc";
}

function compareNullableNumbers(
  left: number | null | undefined,
  right: number | null | undefined,
  direction: PositionLifecycleSortDirection
): number {
  const leftValue = typeof left === "number" && Number.isFinite(left) ? left : null;
  const rightValue = typeof right === "number" && Number.isFinite(right) ? right : null;

  if (leftValue == null && rightValue == null) {
    return 0;
  }
  if (leftValue == null) {
    return 1;
  }
  if (rightValue == null) {
    return -1;
  }
  return direction === "asc" ? leftValue - rightValue : rightValue - leftValue;
}

function compareStrings(
  left: string,
  right: string,
  direction: PositionLifecycleSortDirection
): number {
  return direction === "asc" ? left.localeCompare(right) : right.localeCompare(left);
}

function comparePositionLifecycleRows(
  left: PositionLifecycleRow,
  right: PositionLifecycleRow,
  sortKey: PositionLifecycleSortKey,
  sortDirection: PositionLifecycleSortDirection
): number {
  let result = 0;

  switch (sortKey) {
    case "symbol":
      result = compareStrings(left.symbol, right.symbol, sortDirection);
      break;
    case "entryTime":
      result = compareNullableNumbers(toTimeValue(left.entryTs), toTimeValue(right.entryTs), sortDirection);
      break;
    case "endTime":
      result = compareNullableNumbers(getLifecycleSortFallbackTime(left), getLifecycleSortFallbackTime(right), sortDirection);
      break;
    case "qty":
      result = compareNullableNumbers(left.qty, right.qty, sortDirection);
      break;
    case "entryPrice":
      result = compareNullableNumbers(left.entryPrice, right.entryPrice, sortDirection);
      break;
    case "endPrice":
      result = compareNullableNumbers(getLifecycleDisplayEndPrice(left), getLifecycleDisplayEndPrice(right), sortDirection);
      break;
    case "pnl":
      result = compareNullableNumbers(left.pnl, right.pnl, sortDirection);
      break;
    case "returnPct":
      result = compareNullableNumbers(left.returnPct, right.returnPct, sortDirection);
      break;
    case "holdingDays":
      result = compareNullableNumbers(left.holdingDays, right.holdingDays, sortDirection);
      break;
  }

  if (result !== 0) {
    return result;
  }

  const fallbackByRecentEnd = compareNullableNumbers(
    getLifecycleSortFallbackTime(left),
    getLifecycleSortFallbackTime(right),
    "desc"
  );
  if (fallbackByRecentEnd !== 0) {
    return fallbackByRecentEnd;
  }

  const fallbackBySymbol = left.symbol.localeCompare(right.symbol);
  if (fallbackBySymbol !== 0) {
    return fallbackBySymbol;
  }

  return left.sequence - right.sequence;
}

function buildSymbolPnlRows(run: BacktestDetailOut): SymbolPnlRow[] {
  const positions = run.latest_snapshot?.positions || {};
  const bySymbol = new Map<
    string,
    { qty: number; avgCost: number; realizedPnl: number; tradeCount: number }
  >();

  const orderedTransactions = sortTransactionsChronologically(run.transactions);

  orderedTransactions.forEach((txn) => {
    const symbol = txn.symbol.toUpperCase();
    const state = bySymbol.get(symbol) || { qty: 0, avgCost: 0, realizedPnl: 0, tradeCount: 0 };
    const fee = typeof txn.fee === "number" ? txn.fee : 0;
    state.tradeCount += 1;

    if (txn.side === "BUY") {
      const currentCost = state.qty * state.avgCost;
      const boughtCost = txn.qty * txn.price + fee;
      const newQty = state.qty + txn.qty;
      state.avgCost = newQty > 0 ? (currentCost + boughtCost) / newQty : 0;
      state.qty = newQty;
      bySymbol.set(symbol, state);
      return;
    }

    if (txn.side === "SELL") {
      const sellQty = Math.min(state.qty, txn.qty);
      const proceeds = sellQty * txn.price - fee;
      const costBasis = sellQty * state.avgCost;
      state.realizedPnl += proceeds - costBasis;
      state.qty = Math.max(state.qty - sellQty, 0);
      if (state.qty <= 1e-9) {
        state.qty = 0;
        state.avgCost = 0;
      }
      bySymbol.set(symbol, state);
    }
  });

  const rows: SymbolPnlRow[] = [];
  bySymbol.forEach((state, symbol) => {
    const positionPayload = normalizePositionPayload(positions[symbol]);
    const netQty = positionPayload?.qty ?? state.qty;
    const marketValue = positionPayload?.marketValue ?? 0;
    const lastPrice = positionPayload?.close ?? null;
    const unrealizedPnl = netQty > 0 ? marketValue - netQty * state.avgCost : 0;
    rows.push({
      symbol,
      realizedPnl: state.realizedPnl,
      unrealizedPnl,
      totalPnl: state.realizedPnl + unrealizedPnl,
      netQty,
      marketValue,
      lastPrice,
      tradeCount: state.tradeCount,
    });
  });

  return rows.sort((left, right) => {
    const leftPositive = left.totalPnl >= 0;
    const rightPositive = right.totalPnl >= 0;

    if (leftPositive !== rightPositive) {
      return leftPositive ? -1 : 1;
    }

    if (leftPositive && rightPositive) {
      return right.totalPnl - left.totalPnl;
    }

    return left.totalPnl - right.totalPnl;
  });
}

function buildEventMarkers(
  normalizedPoints: Array<{ ts: string; equity: number }>,
  width: number,
  height: number,
  paddingLeft: number,
  paddingTop: number,
  paddingBottom: number,
  minValue: number,
  maxValue: number,
  signals: BacktestSignalOut[],
  transactions: BacktestTransactionOut[],
  locale: string
): ChartMarker[] {
  if (normalizedPoints.length === 0) {
    return [];
  }

  const xStep = normalizedPoints.length > 1 ? (width - paddingLeft * 2) / (normalizedPoints.length - 1) : 0;
  const pointTimes = normalizedPoints.map((point) => toTimeValue(point.ts));
  const pointDateKeys = normalizedPoints.map((point) => toTradeDateKey(point.ts));
  const firstPointTime = pointTimes[0] ?? null;
  const lastPointTime = pointTimes[pointTimes.length - 1] ?? null;
  const indexByDateKey = new Map<string, number>();
  const occupancy = new Map<string, number>();

  pointDateKeys.forEach((dateKey, index) => {
    if (dateKey && !indexByDateKey.has(dateKey)) {
      indexByDateKey.set(dateKey, index);
    }
  });

  const indexForTime = (rawTs?: string | null) => {
    const dateKey = toTradeDateKey(rawTs);
    if (dateKey) {
      const exactIndex = indexByDateKey.get(dateKey);
      if (typeof exactIndex === "number") {
        return exactIndex;
      }
    }

    const target = toTimeValue(rawTs);
    if (target == null) {
      return null;
    }
    if (firstPointTime != null && target < firstPointTime) {
      return null;
    }
    if (lastPointTime != null && target > lastPointTime) {
      return null;
    }
    let bestIndex = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    for (let index = 0; index < pointTimes.length; index += 1) {
      const candidate = pointTimes[index];
      if (candidate == null) {
        continue;
      }
      const distance = Math.abs(candidate - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    }
    return bestIndex >= 0 ? bestIndex : null;
  };

  const pointPosition = (index: number) => {
    const x = paddingLeft + index * xStep;
    const y = projectValueToChartY(
      normalizedPoints[index].equity,
      height,
      paddingTop,
      paddingBottom,
      minValue,
      maxValue
    );
    return { x, y };
  };

  const markerY = (baseY: number, direction: "above" | "below", offset: number) => {
    const rawY = direction === "above" ? baseY - offset : baseY + offset;
    const minY = paddingTop + 8;
    const maxY = height - paddingBottom - 8;
    return Math.max(minY, Math.min(maxY, rawY));
  };

  const markers: ChartMarker[] = [];

  const groupedSignals = new Map<
    string,
    { ts: string; signal: "BUY" | "SELL"; items: BacktestSignalOut[] }
  >();
  signals.forEach((signal) => {
    if (signal.signal !== "BUY" && signal.signal !== "SELL") {
      return;
    }
    const dateKey = toTradeDateKey(signal.ts);
    if (!dateKey || !signal.ts) {
      return;
    }
    const groupKey = `${dateKey}-${signal.signal}`;
    const existing = groupedSignals.get(groupKey);
    if (existing) {
      existing.items.push(signal);
      return;
    }
    groupedSignals.set(groupKey, {
      ts: signal.ts,
      signal: signal.signal,
      items: [signal],
    });
  });

  Array.from(groupedSignals.values())
    .sort((left, right) => (toTimeValue(left.ts) || 0) - (toTimeValue(right.ts) || 0))
    .forEach((group) => {
      const index = indexForTime(group.ts);
      if (index == null) {
        return;
      }
      const isBuy = group.signal === "BUY";
      const stackKey = `signal-${isBuy ? "buy" : "sell"}-${index}`;
      const stack = occupancy.get(stackKey) || 0;
      occupancy.set(stackKey, stack + 1);
      const position = pointPosition(index);
      markers.push({
        key: `signal-${group.signal}-${group.ts}`,
        x: position.x,
        y: markerY(position.y, isBuy ? "above" : "below", 8 + stack * 8),
        category: isBuy ? "buy_signal" : "sell_signal",
        shape: "circle",
        stroke: isBuy ? "#2563eb" : "#d97706",
        fill: "rgba(15, 23, 42, 0.96)",
        title:
          locale === "zh-CN"
            ? `${group.signal} 信号 ${group.items.length} 个`
            : `${group.signal} Signals (${group.items.length})`,
        details: group.items.map((signal) =>
          signal.reason ? `${signal.symbol}: ${signal.reason}` : signal.symbol
        ),
      });
    });

  const groupedTransactions = new Map<
    string,
    { ts: string; side: "BUY" | "SELL"; items: BacktestTransactionOut[] }
  >();
  transactions.forEach((txn) => {
    if (txn.side !== "BUY" && txn.side !== "SELL") {
      return;
    }
    const dateKey = toTradeDateKey(txn.ts);
    if (!dateKey || !txn.ts) {
      return;
    }
    const groupKey = `${dateKey}-${txn.side}`;
    const existing = groupedTransactions.get(groupKey);
    if (existing) {
      existing.items.push(txn);
      return;
    }
    groupedTransactions.set(groupKey, {
      ts: txn.ts,
      side: txn.side,
      items: [txn],
    });
  });

  Array.from(groupedTransactions.values())
    .sort((left, right) => (toTimeValue(left.ts) || 0) - (toTimeValue(right.ts) || 0))
    .forEach((group) => {
      const index = indexForTime(group.ts);
      if (index == null) {
        return;
      }
      const isBuy = group.side === "BUY";
      const stackKey = `transaction-${isBuy ? "buy" : "sell"}-${index}`;
      const stack = occupancy.get(stackKey) || 0;
      occupancy.set(stackKey, stack + 1);
      const position = pointPosition(index);
      markers.push({
        key: `transaction-${group.side}-${group.ts}`,
        x: position.x,
        y: markerY(position.y, isBuy ? "below" : "above", 12 + stack * 7),
        category: isBuy ? "buy_fill" : "sell_fill",
        shape: isBuy ? "triangle-up" : "triangle-down",
        stroke: isBuy ? "#16a34a" : "#dc2626",
        fill: isBuy ? "#16a34a" : "#dc2626",
        title:
          locale === "zh-CN"
            ? `${group.side} 成交 ${group.items.length} 个`
            : `${group.side} Fills (${group.items.length})`,
        details: group.items.map(
          (txn) =>
            `${txn.symbol} ${txn.qty.toLocaleString(locale, { maximumFractionDigits: 4 })} @ ${txn.price.toLocaleString(locale, {
              maximumFractionDigits: 2,
            })}`
        ),
      });
    });

  return markers;
}

function RunOverviewPanel({ run }: { run: BacktestDetailOut }) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  return (
    <div
      style={{
        marginBottom: 18,
        padding: 18,
        borderRadius: 18,
        background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
        border: "1px solid rgba(71, 85, 105, 0.3)",
        color: "#e2e8f0",
      }}
    >
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 22 }}>
          {isZh ? "Run 概览" : "Run Overview"}
        </h3>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        <Badge tone="info">{run.mode}</Badge>
        {run.basket_name ? <Badge>{run.basket_name}</Badge> : null}
        <Badge
          tone={
            run.status === "completed"
              ? "success"
              : run.status === "failed"
                ? "warning"
                : "neutral"
          }
        >
          {run.status}
        </Badge>
        <Badge>v{run.strategy_version}</Badge>
      </div>

      <div style={infoGridStyle}>
        <div>
          <div style={labelStyle}>Run ID</div>
          <div style={valueStyle}>{run.id}</div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "策略" : "Strategy"}</div>
          <div style={valueStyle}>{run.strategy_name || run.strategy_id}</div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "区间" : "Window"}</div>
          <div style={valueStyle}>
            {run.window_start} {"->"} {run.window_end}
          </div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "股票组合" : "Basket"}</div>
          <div style={valueStyle}>
            {run.basket_name || (isZh ? "沿用策略原始股票池" : "Use the strategy's original universe")}
          </div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "初始资金" : "Initial Cash"}</div>
          <div style={valueStyle}>{formatCurrency(run.initial_cash, locale)}</div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "期末权益" : "Final Equity"}</div>
          <div style={valueStyle}>{formatCurrency(run.final_equity, locale)}</div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "完成时间" : "Completed At"}</div>
          <div style={valueStyle}>{formatDateTime(run.finished_at || run.requested_at, locale)}</div>
        </div>
        <div>
          <div style={labelStyle}>{isZh ? "总耗时" : "Runtime"}</div>
          <div style={valueStyle}>{formatDurationMs(run.runtime_ms, locale)}</div>
        </div>
      </div>

      {run.latest_snapshot ? (
        <div
          style={{
            marginTop: 16,
            padding: 16,
            borderRadius: 16,
            background: "rgba(15, 23, 42, 0.72)",
            border: "1px solid rgba(71, 85, 105, 0.28)",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          <div style={{ marginBottom: 10, fontWeight: 700, color: "#f8fafc" }}>
            {isZh ? "最新快照" : "Latest Snapshot"}
          </div>
          <div style={{ display: "grid", gap: 8, color: "rgba(148, 163, 184, 0.9)" }}>
            <div>{isZh ? "时间" : "Time"}: {formatDateTime(run.latest_snapshot.ts || null, locale)}</div>
            <div>{isZh ? "现金" : "Cash"}: {formatCurrency(run.latest_snapshot.cash, locale)}</div>
            <div>{isZh ? "权益" : "Equity"}: {formatCurrency(run.latest_snapshot.equity, locale)}</div>
            <div>{isZh ? "回撤" : "Drawdown"}: {formatPercent(run.latest_snapshot.drawdown ?? null, 2)}</div>
          </div>
        </div>
      ) : null}

      {run.error_message ? (
        <div
          style={{
            marginTop: 16,
            padding: 14,
            borderRadius: 14,
            background: "rgba(127, 29, 29, 0.28)",
            border: "1px solid rgba(248, 113, 113, 0.18)",
            color: "#fecaca",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {run.error_message}
        </div>
      ) : null}
    </div>
  );
}

function SymbolPnlCard({ rows }: { rows: SymbolPnlRow[] }) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [showAllSymbols, setShowAllSymbols] = useState(false);
  const visibleRows = showAllSymbols ? rows : rows.slice(0, 20);

  return (
    <div style={{ marginTop: 18 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <div>
          <h3 style={{ margin: "0 0 8px", fontSize: 22 }}>
            {isZh ? "个股盈亏" : "Per-Symbol PnL"}
          </h3>
          <p style={sectionSubtitleStyle}>
            {isZh
              ? "按单只股票汇总本次策略运行期间的已实现和未实现盈亏，未平仓按最后一天价格估值"
              : "Summarize realized and unrealized PnL by symbol across this run, valuing open positions with the last available price."}
          </p>
        </div>
        {rows.length > 20 ? (
          <button
            type="button"
            onClick={() => setShowAllSymbols((current) => !current)}
            style={tableToggleButtonStyle}
          >
            {showAllSymbols
              ? isZh
                ? "收起到前 20 支"
                : "Show Top 20"
              : isZh
                ? "展开全部"
                : "Show All"}
          </button>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <div style={emptyStateStyle}>
          {isZh
            ? "这次回测还没有可统计的个股盈亏"
            : "This backtest does not have symbol-level PnL to summarize yet"}
        </div>
      ) : (
        <>
          <div style={{ marginBottom: 10, color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
            {isZh
              ? `当前显示 ${visibleRows.length} / ${rows.length} 支股票，先按盈利从大到小，再按亏损从小到大排序。`
              : `Showing ${visibleRows.length} / ${rows.length} symbols, with winners first from highest gain to lowest, followed by losers from most negative to least negative.`}
          </div>
          <div
            style={{
              overflowX: "auto",
              borderRadius: 18,
              border: "1px solid rgba(71, 85, 105, 0.28)",
              background: "rgba(15, 23, 42, 0.74)",
            }}
          >
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                minWidth: 860,
                fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              <thead>
                <tr
                  style={{
                    background: "rgba(30, 41, 59, 0.9)",
                    color: "rgba(148, 163, 184, 0.88)",
                    textAlign: "left",
                  }}
                >
                  {(isZh
                    ? ["标的", "总盈亏", "已实现", "未实现", "持仓数量", "最新价格", "持仓市值", "交易次数"]
                    : ["Symbol", "Total PnL", "Realized", "Unrealized", "Net Qty", "Last Price", "Market Value", "Trades"]
                  ).map((label) => (
                    <th
                      key={label}
                      style={{
                        padding: "12px 14px",
                        fontSize: 12,
                        fontWeight: 700,
                        letterSpacing: "0.03em",
                        textTransform: "uppercase",
                      }}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const totalTone = row.totalPnl >= 0 ? "#15803d" : "#b91c1c";
                  return (
                    <tr key={row.symbol} style={{ borderBottom: "1px solid rgba(241, 245, 249, 1)" }}>
                      <td style={cellStyle}>{row.symbol}</td>
                      <td style={{ ...cellStyle, color: totalTone, fontWeight: 700 }}>{formatCurrency(row.totalPnl, locale)}</td>
                      <td style={cellStyle}>{formatCurrency(row.realizedPnl, locale)}</td>
                      <td style={cellStyle}>{formatCurrency(row.unrealizedPnl, locale)}</td>
                      <td style={cellStyle}>{row.netQty.toLocaleString(locale, { maximumFractionDigits: 4 })}</td>
                      <td style={cellStyle}>{formatCurrency(row.lastPrice, locale)}</td>
                      <td style={cellStyle}>{formatCurrency(row.marketValue, locale)}</td>
                      <td style={cellStyle}>{row.tradeCount}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function EquityCurveCard({
  run,
  points,
  signals,
  transactions,
  initialCash,
}: {
  run: BacktestDetailOut;
  points: BacktestSnapshotPoint[];
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
  initialCash?: number | null;
}) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [hoveredMarkerKey, setHoveredMarkerKey] = useState<string | null>(null);
  const [chartZoom, setChartZoom] = useState(1);
  const [windowOffset, setWindowOffset] = useState(0);
  const [curveVisibility, setCurveVisibility] = useState<CurveVisibility>({
    strategy: true,
    SPY: true,
    QQQ: true,
  });
  const [markerVisibility, setMarkerVisibility] = useState<MarkerVisibility>({
    buy_signal: false,
    sell_signal: false,
    buy_fill: true,
    sell_fill: true,
  });
  const normalizedPoints = points.filter(
    (point): point is BacktestSnapshotPoint & { ts: string; equity: number } =>
      typeof point.ts === "string" &&
      typeof point.equity === "number" &&
      Number.isFinite(point.equity)
  );
  const values = normalizedPoints.map((point) => point.equity);
  const symbolPnlRows = useMemo(() => buildSymbolPnlRows(run), [run]);
  const comparisonCurves = run.comparison_curves || {};
  const normalizedComparisonCurves = useMemo(() => {
    const buildMap = (pointsInput: BacktestComparisonCurvePoint[] | undefined) =>
      new Map(
        (pointsInput || [])
          .filter(
            (point): point is BacktestComparisonCurvePoint & { ts: string; equity: number } =>
              typeof point.ts === "string" &&
              typeof point.equity === "number" &&
              Number.isFinite(point.equity)
          )
          .map((point) => [point.ts, point.equity])
      );

    return {
      SPY: buildMap(comparisonCurves.SPY),
      QQQ: buildMap(comparisonCurves.QQQ),
    };
  }, [comparisonCurves.QQQ, comparisonCurves.SPY]);
  const spyTotalReturn = useMemo(() => {
    const pointsInput = comparisonCurves.SPY || [];
    const lastPoint = pointsInput[pointsInput.length - 1];
    return typeof lastPoint?.return === "number" && Number.isFinite(lastPoint.return)
      ? lastPoint.return
      : null;
  }, [comparisonCurves.SPY]);
  const qqqTotalReturn = useMemo(() => {
    const pointsInput = comparisonCurves.QQQ || [];
    const lastPoint = pointsInput[pointsInput.length - 1];
    return typeof lastPoint?.return === "number" && Number.isFinite(lastPoint.return)
      ? lastPoint.return
      : null;
  }, [comparisonCurves.QQQ]);
  const benchmarkSymbol =
    run.benchmark_symbol ||
    normalizedPoints.find((point) => typeof point.benchmark_symbol === "string")?.benchmark_symbol ||
    null;
  const benchmarkTotalReturn = metricNumber(run.summary_metrics || {}, "benchmark_total_return");
  const excessReturn = metricNumber(run.summary_metrics || {}, "excess_return");

  const startValue = values[0] ?? initialCash ?? null;
  const endValue = values.length > 0 ? values[values.length - 1] : null;
  const peakValue = values.length > 0 ? Math.max(...values) : null;
  const troughValue = values.length > 0 ? Math.min(...values) : null;

  if (values.length < 2) {
    return (
      <section style={sectionCardStyle}>
        <div style={{ marginBottom: 16 }}>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{isZh ? "权益曲线" : "Equity Curve"}</h2>
          <p style={sectionSubtitleStyle}>
            {isZh
              ? "当前还没有足够的快照点来绘制曲线"
              : "There are not enough snapshot points yet to draw the curve"}
          </p>
        </div>
        <div style={emptyStateStyle}>
          {isZh
            ? "至少需要两条 portfolio snapshot 才能看到走势"
            : "You need at least two portfolio snapshots to see the trend"}
        </div>
      </section>
    );
  }

  const width = 1640;
  const height = 380;
  const axisLeftPadding = 96;
  const axisRightPadding = 28;
  const chartTopPadding = 26;
  const chartBottomPadding = 42;
  const latestPoint = normalizedPoints[normalizedPoints.length - 1];
  const visiblePointCount = Math.max(2, Math.floor(normalizedPoints.length / chartZoom));
  const maxWindowOffset = Math.max(0, normalizedPoints.length - visiblePointCount);
  const safeWindowOffset = Math.min(windowOffset, maxWindowOffset);
  const visiblePoints = normalizedPoints.slice(safeWindowOffset, safeWindowOffset + visiblePointCount);
  const visibleValues = visiblePoints.map((point) => point.equity);
  const visibleSpyValues = visiblePoints
    .map((point) => normalizedComparisonCurves.SPY.get(point.ts))
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const visibleQqqValues = visiblePoints
    .map((point) => normalizedComparisonCurves.QQQ.get(point.ts))
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const combinedVisibleValues = [...visibleValues, ...visibleSpyValues, ...visibleQqqValues];
  const min = Math.min(...combinedVisibleValues);
  const max = Math.max(...combinedVisibleValues);
  const yRange = max - min || 1;
  const linePath = buildLinePath(
    visibleValues,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding,
    min,
    max
  );
  const areaPath = buildAreaPath(
    visibleValues,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding,
    min,
    max
  );
  const spyCurvePoints = visiblePoints.filter((point) =>
    normalizedComparisonCurves.SPY.has(point.ts)
  );
  const qqqCurvePoints = visiblePoints.filter((point) =>
    normalizedComparisonCurves.QQQ.has(point.ts)
  );
  const spyLinePath =
    spyCurvePoints.length > 1
      ? buildLinePath(
          spyCurvePoints.map((point) => normalizedComparisonCurves.SPY.get(point.ts) as number),
          width,
          height,
          axisLeftPadding,
          chartTopPadding,
          chartBottomPadding,
          min,
          max
        )
      : "";
  const qqqLinePath =
    qqqCurvePoints.length > 1
      ? buildLinePath(
          qqqCurvePoints.map((point) => normalizedComparisonCurves.QQQ.get(point.ts) as number),
          width,
          height,
          axisLeftPadding,
          chartTopPadding,
          chartBottomPadding,
          min,
          max
        )
      : "";
  const referenceValue = typeof startValue === "number" ? startValue : null;
  const referenceLineY =
    referenceValue == null
      ? null
      : projectValueToChartY(
          referenceValue,
          height,
          chartTopPadding,
          chartBottomPadding,
          min,
          max
        );
  const visibleWindowStart = visiblePoints[0]?.ts || null;
  const visibleWindowEnd = visiblePoints[visiblePoints.length - 1]?.ts || null;
  const yAxisTicks = buildYAxisTicks(min, max, height, chartTopPadding, chartBottomPadding);
  const markers = buildEventMarkers(
    visiblePoints,
    width,
    height,
    axisLeftPadding,
    chartTopPadding,
    chartBottomPadding,
    min,
    max,
    signals,
    transactions,
    locale
  );
  const visibleMarkers = markers.filter((marker) => markerVisibility[marker.category]);
  const hoveredMarker = hoveredMarkerKey
    ? visibleMarkers.find((marker) => marker.key === hoveredMarkerKey) || null
    : null;
  const buySignalCount = signals.filter((item) => item.signal === "BUY").length;
  const sellSignalCount = signals.filter((item) => item.signal === "SELL").length;
  const buyCount = transactions.filter((item) => item.side === "BUY").length;
  const sellCount = transactions.filter((item) => item.side === "SELL").length;
  const setAllMarkers = (visible: boolean) => {
    setMarkerVisibility({
      buy_signal: visible,
      sell_signal: visible,
      buy_fill: visible,
      sell_fill: visible,
    });
    if (!visible) {
      setHoveredMarkerKey(null);
    }
  };
  const toggleMarkerCategory = (category: keyof MarkerVisibility) => {
    setMarkerVisibility((current) => ({
      ...current,
      [category]: !current[category],
    }));
  };
  const zoomOut = () => {
    setChartZoom((current) => Math.max(1, Number((current - 0.25).toFixed(2))));
    setHoveredMarkerKey(null);
  };
  const zoomIn = () => {
    setChartZoom((current) => Math.min(6, Number((current + 0.25).toFixed(2))));
    setHoveredMarkerKey(null);
  };
  const resetZoom = () => {
    setChartZoom(1);
    setWindowOffset(0);
    setHoveredMarkerKey(null);
  };

  return (
    <section style={sectionCardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-start",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{isZh ? "权益曲线" : "Equity Curve"}</h2>
        </div>
      </div>

      <RunOverviewPanel run={run} />

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 12,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
          {isZh
            ? "放大后会只显示更短的一段时间窗口; 虚线表示起始资金基准线"
            : "Zooming in shows a shorter time window so you can inspect local moves and events more clearly; the dashed line marks the starting capital reference."}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" style={zoomButtonStyle} onClick={zoomOut}>
            {isZh ? "缩小" : "Zoom Out"}
          </button>
          <button type="button" style={zoomButtonStyle} onClick={resetZoom}>
            {isZh ? "还原" : "Reset"}
          </button>
          <button type="button" style={zoomButtonStyle} onClick={zoomIn}>
            {isZh ? "放大" : "Zoom In"}
          </button>
          <span style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
            {isZh ? "缩放" : "Zoom"} {Math.round(chartZoom * 100)}%
          </span>
        </div>
      </div>

      <div
        style={{
          marginBottom: 14,
          display: "grid",
          gap: 8,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
        >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
            color: "rgba(148, 163, 184, 0.88)",
            fontSize: 13,
          }}
        >
          <span>
            {isZh ? "当前窗口" : "Current Window"}: {formatDateTime(visibleWindowStart, locale)} {"->"}{" "}
            {formatDateTime(visibleWindowEnd, locale)}
          </span>
          <span>
            {isZh
              ? `显示 ${visiblePoints.length} / ${normalizedPoints.length} 个快照点`
              : `Showing ${visiblePoints.length} / ${normalizedPoints.length} snapshot points`}
          </span>
        </div>
        <input
          type="range"
          min={0}
          max={maxWindowOffset}
          step={1}
          value={safeWindowOffset}
          disabled={maxWindowOffset === 0}
          onChange={(event) => {
            setWindowOffset(Number(event.target.value));
            setHoveredMarkerKey(null);
          }}
          style={{ width: "100%", accentColor: "#0f766e" }}
        />
      </div>

      <div
        style={{
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
          marginBottom: 14,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <button
          type="button"
          style={markerToggleChipStyle(curveVisibility.strategy, "#0f766e")}
          onClick={() =>
            setCurveVisibility((current) => ({ ...current, strategy: !current.strategy }))
          }
        >
          {isZh ? "策略曲线" : "Strategy Curve"}
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(curveVisibility.SPY, "#2563eb")}
          onClick={() => setCurveVisibility((current) => ({ ...current, SPY: !current.SPY }))}
        >
          SPY
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(curveVisibility.QQQ, "#f97316")}
          onClick={() => setCurveVisibility((current) => ({ ...current, QQQ: !current.QQQ }))}
        >
          QQQ
        </button>
        <button type="button" style={markerToggleButtonStyle(true)} onClick={() => setAllMarkers(true)}>
          {isZh ? "全部开启" : "Enable All"}
        </button>
        <button type="button" style={markerToggleButtonStyle(false)} onClick={() => setAllMarkers(false)}>
          {isZh ? "全部关闭" : "Disable All"}
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.buy_signal, "#2563eb")}
          onClick={() => toggleMarkerCategory("buy_signal")}
        >
          {isZh ? "BUY 信号" : "BUY Signals"}
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.sell_signal, "#d97706")}
          onClick={() => toggleMarkerCategory("sell_signal")}
        >
          {isZh ? "SELL 信号" : "SELL Signals"}
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.buy_fill, "#16a34a")}
          onClick={() => toggleMarkerCategory("buy_fill")}
        >
          {isZh ? "BUY 成交" : "BUY Fills"}
        </button>
        <button
          type="button"
          style={markerToggleChipStyle(markerVisibility.sell_fill, "#dc2626")}
          onClick={() => toggleMarkerCategory("sell_fill")}
        >
          {isZh ? "SELL 成交" : "SELL Fills"}
        </button>
      </div>

      <div
        style={{
          position: "relative",
          padding: 18,
          borderRadius: 18,
          background: "linear-gradient(180deg, rgba(8,15,24,0.95), rgba(15,23,42,0.9))",
          border: "1px solid rgba(94, 234, 212, 0.18)",
        }}
      >
        <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: 380, display: "block" }}>
          <defs>
            <linearGradient id="equityFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="rgba(15,118,110,0.28)" />
              <stop offset="100%" stopColor="rgba(15,118,110,0.02)" />
            </linearGradient>
          </defs>
          {yAxisTicks.map((tick) => (
            <g key={`y-axis-${tick.y}`}>
              <line
                x1={axisLeftPadding}
                x2={width - axisRightPadding}
                y1={tick.y}
                y2={tick.y}
                stroke="rgba(148, 163, 184, 0.18)"
                strokeWidth="1"
              />
              <text
                x={axisLeftPadding - 12}
                y={tick.y + 4}
                textAnchor="end"
                fill="#64748b"
                fontSize="11"
                fontWeight="600"
                fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
              >
                {formatCurrency(tick.value, locale)}
              </text>
            </g>
          ))}
          {referenceLineY != null ? (
            <g>
              <line
                x1={axisLeftPadding}
                x2={width - axisRightPadding}
                y1={referenceLineY}
                y2={referenceLineY}
                stroke="#0f172a"
                strokeDasharray="7 6"
                strokeOpacity="0.32"
                strokeWidth="1.5"
              />
              <text
                x={width - axisRightPadding}
                y={referenceLineY - 8}
                textAnchor="end"
                fill="#0f172a"
                fillOpacity="0.62"
                fontSize="12"
                fontWeight="700"
                fontFamily="Avenir Next, Segoe UI, Helvetica Neue, sans-serif"
              >
                {isZh ? "起始资金" : "Starting Capital"} {formatCurrency(referenceValue, locale)}
              </text>
            </g>
          ) : null}
          {curveVisibility.strategy ? <path d={areaPath} fill="url(#equityFill)" /> : null}
          {curveVisibility.SPY && spyLinePath ? (
            <path
              d={spyLinePath}
              fill="none"
              stroke="#2563eb"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.9"
            />
          ) : null}
          {curveVisibility.QQQ && qqqLinePath ? (
            <path
              d={qqqLinePath}
              fill="none"
              stroke="#f97316"
              strokeWidth="3"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.9"
            />
          ) : null}
          {curveVisibility.strategy ? (
            <path
              d={linePath}
              fill="none"
              stroke="#0f766e"
              strokeWidth="4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
          {visibleMarkers.map((marker) => {
            if (marker.shape === "circle") {
              return (
                <g
                  key={marker.key}
                  onMouseEnter={() => setHoveredMarkerKey(marker.key)}
                  onMouseLeave={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                  onFocus={() => setHoveredMarkerKey(marker.key)}
                  onBlur={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                  style={{ cursor: "pointer" }}
                >
                  <circle
                    cx={marker.x}
                    cy={marker.y}
                    r="5"
                    fill={marker.fill}
                    stroke={marker.stroke}
                    strokeWidth="2.5"
                  />
                  <title>{[marker.title, ...marker.details].join("\n")}</title>
                </g>
              );
            }

            const pointsText =
              marker.shape === "triangle-up"
                ? `${marker.x},${marker.y - 6} ${marker.x - 6},${marker.y + 6} ${marker.x + 6},${marker.y + 6}`
                : `${marker.x},${marker.y + 6} ${marker.x - 6},${marker.y - 6} ${marker.x + 6},${marker.y - 6}`;
            return (
              <g
                key={marker.key}
                onMouseEnter={() => setHoveredMarkerKey(marker.key)}
                onMouseLeave={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                onFocus={() => setHoveredMarkerKey(marker.key)}
                onBlur={() => setHoveredMarkerKey((current) => (current === marker.key ? null : current))}
                style={{ cursor: "pointer" }}
              >
                <polygon points={pointsText} fill={marker.fill} stroke={marker.stroke} strokeWidth="1.5" />
                <title>{[marker.title, ...marker.details].join("\n")}</title>
              </g>
            );
          })}
        </svg>
        {hoveredMarker ? (
          <div
            style={{
              position: "absolute",
              top: `${(hoveredMarker.y / height) * 100}%`,
              ...(hoveredMarker.x / width > 0.68
                ? {
                    right: `${100 - (hoveredMarker.x / width) * 100}%`,
                    transform: "translate(-12px, -100%)",
                  }
                : {
                    left: `${(hoveredMarker.x / width) * 100}%`,
                    transform: "translate(12px, -100%)",
                  }),
              maxWidth: 360,
              maxHeight: 240,
              overflowY: "auto",
              padding: 12,
              borderRadius: 14,
              border: "1px solid rgba(15, 23, 42, 0.08)",
              background: "rgba(15, 23, 42, 0.94)",
              color: "#f8fafc",
              boxShadow: "0 18px 40px rgba(15, 23, 42, 0.24)",
              fontSize: 12,
              lineHeight: 1.5,
              pointerEvents: "none",
              zIndex: 2,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 6 }}>{hoveredMarker.title}</div>
            <div style={{ display: "grid", gap: 4 }}>
              {hoveredMarker.details.map((detail) => (
                <div key={`${hoveredMarker.key}-${detail}`}>{detail}</div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div
        style={{
          marginTop: 12,
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          color: "rgba(148, 163, 184, 0.88)",
          fontSize: 13,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        {curveVisibility.strategy ? (
          <span style={legendItemStyle}>
            <span style={legendLineStyle("#0f766e")} />
            {isZh ? "策略曲线" : "Strategy Curve"}
          </span>
        ) : null}
        {curveVisibility.SPY && spyLinePath ? (
          <span style={legendItemStyle}>
            <span style={legendLineStyle("#2563eb")} />
            SPY
          </span>
        ) : null}
        {curveVisibility.QQQ && qqqLinePath ? (
          <span style={legendItemStyle}>
            <span style={legendLineStyle("#f97316")} />
            QQQ
          </span>
        ) : null}
        <span style={legendItemStyle}>
          <span style={{ ...legendDotStyle, border: "2px solid #2563eb", background: "rgba(15, 23, 42, 0.92)" }} />
          {isZh ? "BUY 信号" : "BUY Signals"} {buySignalCount}
        </span>
        <span style={legendItemStyle}>
          <span style={{ ...legendDotStyle, border: "2px solid #d97706", background: "rgba(15, 23, 42, 0.92)" }} />
          {isZh ? "SELL 信号" : "SELL Signals"} {sellSignalCount}
        </span>
        <span style={legendItemStyle}>
          <span style={legendTriangleUpStyle} />
          {isZh ? "BUY 成交" : "BUY Fills"} {buyCount}
        </span>
        <span style={legendItemStyle}>
          <span style={legendTriangleDownStyle} />
          {isZh ? "SELL 成交" : "SELL Fills"} {sellCount}
        </span>
      </div>

      <div
        style={{
          marginTop: 14,
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "快照点数" : "Snapshots"}</div>
          <div style={miniMetricValueStyle}>{normalizedPoints.length}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "曲线峰值" : "Peak Equity"}</div>
          <div style={miniMetricValueStyle}>{formatCurrency(peakValue, locale)}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "曲线谷值" : "Lowest Equity"}</div>
          <div style={miniMetricValueStyle}>{formatCurrency(troughValue, locale)}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>SPY {isZh ? "收益" : "Return"}</div>
          <div style={miniMetricValueStyle}>{formatPercent(spyTotalReturn, 2)}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>QQQ {isZh ? "收益" : "Return"}</div>
          <div style={miniMetricValueStyle}>{formatPercent(qqqTotalReturn, 2)}</div>
        </div>
        {benchmarkSymbol ? (
          <div style={miniMetricStyle}>
            <div style={labelStyle}>
              {isZh ? "基准收益" : "Benchmark Return"} {benchmarkSymbol ? `(${benchmarkSymbol})` : ""}
            </div>
            <div style={miniMetricValueStyle}>{formatPercent(benchmarkTotalReturn, 2)}</div>
          </div>
        ) : null}
        {benchmarkSymbol ? (
          <div style={miniMetricStyle}>
            <div style={labelStyle}>{isZh ? "超额收益" : "Excess Return"}</div>
            <div style={miniMetricValueStyle}>{formatPercent(excessReturn, 2)}</div>
          </div>
        ) : null}
          <div style={miniMetricStyle}>
            <div style={labelStyle}>{isZh ? "最后快照" : "Last Snapshot"}</div>
            <div style={miniMetricValueStyle}>{formatDateTime(latestPoint?.ts || null, locale)}</div>
          </div>
        </div>

      <SymbolPnlCard rows={symbolPnlRows} />
    </section>
  );
}

function EquityCurveCardLightweight({
  run,
  points,
  signals,
  transactions,
  initialCash,
}: {
  run: BacktestDetailOut;
  points: BacktestEquityPoint[];
  signals: BacktestSignalOut[];
  transactions: BacktestTransactionOut[];
  initialCash?: number | null;
}) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [curveVisibility, setCurveVisibility] = useState<CurveVisibility>({
    strategy: true,
    SPY: true,
    QQQ: true,
  });
  const [markerVisibility, setMarkerVisibility] = useState<MarkerVisibility>({
    buy_signal: false,
    sell_signal: false,
    buy_fill: true,
    sell_fill: true,
  });
  const normalizedPoints = useMemo(() => normalizeEquityPoints(points), [points]);
  const values = normalizedPoints.map((point) => point.value);
  const symbolPnlRows = useMemo(() => buildSymbolPnlRows(run), [run]);
  const comparisonCurves = run.comparison_curves || {};
  const comparisons = useMemo(
    () => ([
      {
        key: "SPY" as const,
        color: "#2563eb",
        points: ((comparisonCurves.SPY || []).length > 0
          ? comparisonCurves.SPY || []
          : points
              .filter((point) => point.benchmark_symbol?.toUpperCase() === "SPY")
              .map((point) => ({ ts: point.ts, equity: point.benchmark_equity })))
          .map((point) => ({ time: toChartTime(point.ts), value: point.equity }))
          .filter((point): point is { time: string; value: number } =>
            Boolean(point.time) && typeof point.value === "number" && Number.isFinite(point.value)),
      },
      {
        key: "QQQ" as const,
        color: "#f97316",
        points: ((comparisonCurves.QQQ || []).length > 0
          ? comparisonCurves.QQQ || []
          : points
              .filter((point) => point.benchmark_symbol?.toUpperCase() === "QQQ")
              .map((point) => ({ ts: point.ts, equity: point.benchmark_equity })))
          .map((point) => ({ time: toChartTime(point.ts), value: point.equity }))
          .filter((point): point is { time: string; value: number } =>
            Boolean(point.time) && typeof point.value === "number" && Number.isFinite(point.value)),
      },
    ]),
    [comparisonCurves.QQQ, comparisonCurves.SPY, points],
  );
  const markers = useMemo(
    () => buildEquityEventMarkers(
      normalizedPoints,
      signals.map((signal) => ({
        id: signal.id,
        ts: signal.ts,
        symbol: signal.symbol,
        action: signal.signal,
        reason: signal.reason,
      })),
      transactions.map((transaction) => ({
        id: transaction.id,
        ts: transaction.ts,
        symbol: transaction.symbol,
        action: transaction.side,
        reason: `${formatCurrency(transaction.price, locale)} × ${transaction.qty.toLocaleString(locale, { maximumFractionDigits: 4 })}`,
      })),
      locale,
    ),
    [locale, normalizedPoints, signals, transactions],
  );
  const setAllMarkers = (visible: boolean) => setMarkerVisibility({
    buy_signal: visible,
    sell_signal: visible,
    buy_fill: visible,
    sell_fill: visible,
  });
  const startValue = values[0] ?? initialCash ?? null;
  const peakValue = values.length ? Math.max(...values) : null;
  const troughValue = values.length ? Math.min(...values) : null;
  const latestPoint = normalizedPoints[normalizedPoints.length - 1];
  const benchmarkSymbol = run.benchmark_symbol || points.find((point) => point.benchmark_symbol)?.benchmark_symbol || null;
  const benchmarkTotalReturn = metricNumber(run.summary_metrics || {}, "benchmark_total_return");
  const excessReturn = metricNumber(run.summary_metrics || {}, "excess_return");
  const comparisonReturn = (key: "SPY" | "QQQ") => {
    const source = comparisonCurves[key] || [];
    const value = source[source.length - 1]?.return;
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };

  if (normalizedPoints.length < 2) {
    return (
      <section style={sectionCardStyle}>
        <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{isZh ? "权益曲线" : "Equity Curve"}</h2>
        <div style={emptyStateStyle}>
          {isZh ? "至少需要两个有效权益点才能绘图" : "At least two valid equity points are required"}
        </div>
      </section>
    );
  }

  return (
    <section style={sectionCardStyle}>
      <h2 style={{ margin: "0 0 16px", fontSize: 24 }}>{isZh ? "权益曲线" : "Equity Curve"}</h2>
      <RunOverviewPanel run={run} />
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "14px 0" }}>
        <button type="button" style={markerToggleChipStyle(curveVisibility.strategy, "#0f766e")} onClick={() => setCurveVisibility((current) => ({ ...current, strategy: !current.strategy }))}>
          {isZh ? "策略曲线" : "Strategy Curve"}
        </button>
        {(["SPY", "QQQ"] as const).map((key) => (
          <button key={key} type="button" style={markerToggleChipStyle(curveVisibility[key], key === "SPY" ? "#2563eb" : "#f97316")} onClick={() => setCurveVisibility((current) => ({ ...current, [key]: !current[key] }))}>
            {key}
          </button>
        ))}
        <button type="button" style={markerToggleButtonStyle(true)} onClick={() => setAllMarkers(true)}>{isZh ? "全部开启" : "Enable All"}</button>
        <button type="button" style={markerToggleButtonStyle(false)} onClick={() => setAllMarkers(false)}>{isZh ? "全部关闭" : "Disable All"}</button>
        {([
          ["buy_signal", isZh ? "BUY 信号" : "BUY Signals", "#2563eb"],
          ["sell_signal", isZh ? "SELL 信号" : "SELL Signals", "#d97706"],
          ["buy_fill", isZh ? "BUY 成交" : "BUY Fills", "#16a34a"],
          ["sell_fill", isZh ? "SELL 成交" : "SELL Fills", "#dc2626"],
        ] as const).map(([key, label, color]) => (
          <button key={key} type="button" style={markerToggleChipStyle(markerVisibility[key], color)} onClick={() => setMarkerVisibility((current) => ({ ...current, [key]: !current[key] }))}>
            {label}
          </button>
        ))}
      </div>
      <EquityLightweightChart
        points={normalizedPoints}
        comparisons={comparisons}
        markers={markers}
        strategyVisible={curveVisibility.strategy}
        comparisonVisibility={{ SPY: curveVisibility.SPY, QQQ: curveVisibility.QQQ }}
        markerVisibility={markerVisibility}
        initialValue={startValue}
        locale={locale}
      />
      <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
        <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "快照点数" : "Snapshots"}</div><div style={miniMetricValueStyle}>{normalizedPoints.length}</div></div>
        <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "曲线峰值" : "Peak Equity"}</div><div style={miniMetricValueStyle}>{formatCurrency(peakValue, locale)}</div></div>
        <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "曲线谷值" : "Lowest Equity"}</div><div style={miniMetricValueStyle}>{formatCurrency(troughValue, locale)}</div></div>
        <div style={miniMetricStyle}><div style={labelStyle}>SPY {isZh ? "收益" : "Return"}</div><div style={miniMetricValueStyle}>{formatPercent(comparisonReturn("SPY"), 2)}</div></div>
        <div style={miniMetricStyle}><div style={labelStyle}>QQQ {isZh ? "收益" : "Return"}</div><div style={miniMetricValueStyle}>{formatPercent(comparisonReturn("QQQ"), 2)}</div></div>
        {benchmarkSymbol ? <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "基准收益" : "Benchmark Return"} ({benchmarkSymbol})</div><div style={miniMetricValueStyle}>{formatPercent(benchmarkTotalReturn, 2)}</div></div> : null}
        {benchmarkSymbol ? <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "超额收益" : "Excess Return"}</div><div style={miniMetricValueStyle}>{formatPercent(excessReturn, 2)}</div></div> : null}
        <div style={miniMetricStyle}><div style={labelStyle}>{isZh ? "最后快照" : "Last Snapshot"}</div><div style={miniMetricValueStyle}>{formatDateTime(latestPoint?.sourceTs || null, locale)}</div></div>
      </div>
      <SymbolPnlCard rows={symbolPnlRows} />
    </section>
  );
}

function markerToggleButtonStyle(isPrimary: boolean) {
  return {
    border: "none",
    borderRadius: 999,
    padding: "8px 14px",
    background: isPrimary ? "#0f766e" : "rgba(15, 23, 42, 0.76)",
    color: isPrimary ? "#ffffff" : "#e2e8f0",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

function markerToggleChipStyle(active: boolean, accent: string) {
  return {
    borderRadius: 999,
    padding: "8px 14px",
    border: `1px solid ${active ? accent : "rgba(148, 163, 184, 0.24)"}`,
    background: active ? `${accent}14` : "rgba(15, 23, 42, 0.76)",
    color: active ? accent : "#cbd5e1",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

const zoomButtonStyle = {
  borderRadius: 999,
  padding: "8px 14px",
  border: "1px solid rgba(148, 163, 184, 0.28)",
  background: "rgba(15, 23, 42, 0.76)",
  color: "#e2e8f0",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const tableToggleButtonStyle = {
  borderRadius: 999,
  padding: "8px 14px",
  border: "1px solid rgba(59, 130, 246, 0.24)",
  background: "rgba(30, 64, 175, 0.18)",
  color: "#bfdbfe",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

function lifecyclePnlFilterChipStyle(active: boolean) {
  return {
    borderRadius: 999,
    padding: "8px 12px",
    border: `1px solid ${active ? "rgba(59, 130, 246, 0.42)" : "rgba(148, 163, 184, 0.24)"}`,
    background: active ? "rgba(30, 64, 175, 0.24)" : "rgba(15, 23, 42, 0.72)",
    color: active ? "#dbeafe" : "#e2e8f0",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

function lifecycleLookbackChipStyle(active: boolean) {
  return {
    borderRadius: 999,
    padding: "8px 12px",
    border: `1px solid ${active ? "rgba(34, 197, 94, 0.42)" : "rgba(148, 163, 184, 0.24)"}`,
    background: active ? "rgba(20, 83, 45, 0.88)" : "rgba(15, 23, 42, 0.72)",
    color: active ? "#f0fdf4" : "#e2e8f0",
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  } as const;
}

function classifyLifecyclePnl(row: PositionLifecycleRow): Exclude<PositionLifecyclePnlFilter, "all"> {
  const pnl = row.pnl ?? 0;
  if (pnl > 0) {
    return "profit";
  }
  if (pnl < 0) {
    return "loss";
  }
  return "flat";
}

const lifecycleLookbackInputStyle = {
  width: 72,
  borderRadius: 10,
  border: "1px solid rgba(71, 85, 105, 0.42)",
  background: "rgba(8, 15, 24, 0.92)",
  color: "#f8fafc",
  padding: "7px 10px",
  fontSize: 13,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

function buildLifecycleChartMarkers(
  row: PositionLifecycleRow,
  locale: string,
  isZh: boolean
): LifecycleChartMarker[] {
  const markers: LifecycleChartMarker[] = [];

  if (row.entrySignalTradeDate) {
    markers.push({
      key: `buy-signal-${row.key}`,
      label: isZh ? "买信号" : "Buy Signal",
      date: row.entrySignalTradeDate,
      price: row.entryPrice,
      tone: "buy_signal",
      description: `${isZh ? "买入信号" : "Buy Signal"} ${row.symbol} · ${formatLifecycleEventMoment(
        row.entrySignalTs,
        row.entrySignalTradeDate,
        locale
      )}${
        row.entryPrice != null ? ` · ${formatCurrency(row.entryPrice, locale)}` : ""
      }`,
    });
  }

  if (row.entryTradeDate) {
    markers.push({
      key: `buy-${row.key}`,
      label: isZh ? "买入" : "Buy",
      date: row.entryTradeDate,
      price: row.entryPrice,
      tone: "buy",
      description: `${isZh ? "买入" : "Buy"} ${row.symbol} · ${formatLifecycleEventMoment(
        row.entryTs,
        row.entryTradeDate,
        locale
      )} · ${formatCurrency(row.entryPrice, locale)}`,
    });
  }

  if (row.exitSignalTradeDate) {
    markers.push({
      key: `sell-signal-${row.key}`,
      label: isZh ? "卖信号" : "Sell Signal",
      date: row.exitSignalTradeDate,
      price: row.exitPrice ?? row.markPrice ?? row.entryPrice,
      tone: "sell_signal",
      description: `${isZh ? "卖出信号" : "Sell Signal"} ${row.symbol} · ${formatLifecycleEventMoment(
        row.exitSignalTs,
        row.exitSignalTradeDate,
        locale
      )}${
        row.exitPrice != null
          ? ` · ${formatCurrency(row.exitPrice, locale)}`
          : row.markPrice != null
            ? ` · ${formatCurrency(row.markPrice, locale)}`
            : ""
      }`,
    });
  }

  if (row.exitTradeDate && row.exitPrice != null) {
    markers.push({
      key: `sell-${row.key}`,
      label: isZh ? "卖出" : "Sell",
      date: row.exitTradeDate,
      price: row.exitPrice,
      tone: "sell",
      description: `${isZh ? "卖出" : "Sell"} ${row.symbol} · ${formatLifecycleEventMoment(
        row.exitTs,
        row.exitTradeDate,
        locale
      )} · ${formatCurrency(row.exitPrice, locale)}`,
    });
  } else if (row.markTradeDate && row.markPrice != null) {
    markers.push({
      key: `mark-${row.key}`,
      label: isZh ? "标记" : "Mark",
      date: row.markTradeDate,
      price: row.markPrice,
      tone: "mark",
      description: `${isZh ? "标记价格" : "Marked Price"} ${row.symbol} · ${formatLifecycleEventMoment(
        row.markTs,
        row.markTradeDate,
        locale
      )} · ${formatCurrency(row.markPrice, locale)}`,
    });
  }

  return markers;
}

function findLifecycleSignal(
  signals: BacktestSignalOut[],
  row: PositionLifecycleRow,
  action: "BUY" | "SELL"
) {
  const signalTs = action === "BUY" ? row.entrySignalTs : row.exitSignalTs;
  const signalTradeDate = toTradeDateKey(signalTs);
  const symbol = row.symbol.toUpperCase();

  const exactMatch = signals.find(
    (signal) =>
      signal.symbol.toUpperCase() === symbol
      && signal.signal === action
      && signal.ts === signalTs
  );
  if (exactMatch) {
    return exactMatch;
  }

  if (!signalTradeDate) {
    return null;
  }

  return (
    signals.find(
      (signal) =>
        signal.symbol.toUpperCase() === symbol
        && signal.signal === action
        && toTradeDateKey(signal.ts) === signalTradeDate
    ) || null
  );
}

function extractIslandReversalGapSetup(signal: BacktestSignalOut | null): IslandReversalGapSetup | null {
  const features = getObjectValue(signal?.features);
  const setup = getObjectValue(features?.setup);
  if (!setup) {
    return null;
  }

  const leftGapTradeDate = getRecordText(setup, "left_gap_trade_date");
  const breakoutTradeDate = getRecordText(setup, "breakout_trade_date");
  const islandHigh = getRecordNumber(setup, "island_high");
  const breakoutGapLow = getRecordNumber(setup, "breakout_gap_low");

  if (!leftGapTradeDate || !breakoutTradeDate || islandHigh == null || breakoutGapLow == null) {
    return null;
  }

  return {
    leftGapTradeDate,
    breakoutTradeDate,
    islandHigh,
    breakoutGapLow,
    leftGapPct: getRecordNumber(setup, "left_gap_pct"),
    breakoutGapPct: getRecordNumber(setup, "breakout_gap_pct"),
  };
}

function extractDoubleBottomSetup(signal: BacktestSignalOut | null): DoubleBottomSetup | null {
  const features = getObjectValue(signal?.features);
  const setup = getObjectValue(features?.setup);
  if (!setup) {
    return null;
  }

  const leftBottomTradeDate = getRecordText(setup, "left_bottom_trade_date");
  const necklineTradeDate = getRecordText(setup, "neckline_trade_date");
  const rightBottomTradeDate = getRecordText(setup, "right_bottom_trade_date");
  const breakoutTradeDate = getRecordText(setup, "breakout_trade_date");
  const leftBottomLow = getRecordNumber(setup, "left_bottom_low");
  const rightBottomLow = getRecordNumber(setup, "right_bottom_low");

  if (!leftBottomTradeDate || !rightBottomTradeDate || leftBottomLow == null || rightBottomLow == null) {
    return null;
  }

  return {
    leftBottomTradeDate,
    rightBottomTradeDate,
    leftBottomLow,
    rightBottomLow,
    necklineTradeDate,
    necklinePrice: getRecordNumber(setup, "neckline_price"),
    breakoutTradeDate,
    breakoutClose: getRecordNumber(setup, "breakout_close"),
    breakoutWaitBars: getRecordNumber(setup, "breakout_wait_bars"),
    breakoutVolumeRatio: getRecordNumber(setup, "breakout_volume_ratio"),
    bottomDistancePct: getRecordNumber(setup, "bottom_distance_pct"),
  };
}

function extractSupportResistanceSetup(
  signal: BacktestSignalOut | null
): SupportResistanceSignalSetup | null {
  const features = getObjectValue(signal?.features);
  const supportResistance = getObjectValue(features?.support_resistance);
  const zone = getObjectValue(supportResistance?.zone);
  const evidence = getObjectValue(supportResistance?.score_evidence);
  if (!supportResistance || !zone) return null;
  const zoneKey = getRecordText(supportResistance, "zone_key");
  const selectedSetup = getRecordText(supportResistance, "selected_setup");
  const lower = getRecordNumber(zone, "lower");
  const upper = getRecordNumber(zone, "upper");
  if (!zoneKey || !selectedSetup || lower == null || upper == null) return null;
  return {
    zoneKey,
    selectedSetup,
    lower,
    upper,
    validFrom: getRecordText(zone, "valid_from"),
    score: typeof signal?.score === "number" ? signal.score : null,
    resolvedSamples: evidence ? getRecordNumber(evidence, "resolved_samples") : null,
  };
}

function buildSupportResistanceMarkers(
  events: SupportResistanceRunEventOut[],
  locale: string,
  isZh: boolean
): LifecycleChartMarker[] {
  const toneByType: Record<string, LifecycleChartMarker["tone"]> = {
    touch: "neckline",
    breakout: "breakout",
    retest: "right_bottom",
    candidate: "mark",
    selection: "breakout",
    role_transition: "right_bottom",
  } as Record<string, LifecycleChartMarker["tone"]>;
  return events
    .filter((event) => isDisplayableSupportResistanceEventType(event.event_type))
    .map((event) => {
      const payload = event.payload || {};
      const zone = getObjectValue(payload.zone);
      const lower = event.lower_price ?? (zone ? getRecordNumber(zone, "lower") : null);
      const upper = event.upper_price ?? (zone ? getRecordNumber(zone, "upper") : null);
      const price = upper ?? lower;
      const labelMap: Record<string, string> = {
        touch: isZh ? "触碰" : "Touch",
        breakout: isZh ? "突破" : "Breakout",
        retest: isZh ? "回踩" : "Retest",
        candidate: isZh ? "候选" : "Candidate",
        selection: isZh ? "选中" : "Selected",
        role_transition: isZh ? "角色转换" : "Role Flip",
      };
      return {
        key: `sr-${event.id}`,
        label: labelMap[event.event_type] || event.event_type,
        date: event.event_date,
        price,
        tone: toneByType[event.event_type] || "neckline",
        description: [
          labelMap[event.event_type] || event.event_type,
          event.setup,
          price != null ? formatCurrency(price, locale) : null,
          event.score != null ? `${isZh ? "后验" : "Posterior"} ${formatPercent(event.score, 2)}` : null,
          event.posterior_sample_count != null ? `n=${event.posterior_sample_count}` : null,
        ].filter(Boolean).join(" · "),
      };
    });
}

function buildDoubleBottomSetupMarkers(
  setup: DoubleBottomSetup | null,
  locale: string,
  isZh: boolean
): LifecycleChartMarker[] {
  if (!setup) {
    return [];
  }

  const sharedParts = [
    setup.bottomDistancePct != null
      ? `${isZh ? "底部价差" : "Bottom Spread"} ${formatPercent(setup.bottomDistancePct, 2)}`
      : null,
    setup.necklinePrice != null
      ? `${isZh ? "颈线" : "Neckline"} ${formatCurrency(setup.necklinePrice, locale)}`
      : null,
  ].filter(Boolean);

  const markers: LifecycleChartMarker[] = [
    {
      key: `left-bottom-${setup.leftBottomTradeDate}`,
      label: isZh ? "左底" : "Left Bottom",
      date: setup.leftBottomTradeDate,
      price: setup.leftBottomLow,
      tone: "left_bottom",
      description: [
        isZh ? "双底左底" : "Double-Bottom Left Low",
        setup.leftBottomTradeDate,
        formatCurrency(setup.leftBottomLow, locale),
        ...sharedParts,
      ].join(" · "),
    },
    {
      key: `right-bottom-${setup.rightBottomTradeDate}`,
      label: isZh ? "右底" : "Right Bottom",
      date: setup.rightBottomTradeDate,
      price: setup.rightBottomLow,
      tone: "right_bottom",
      description: [
        isZh ? "双底右底" : "Double-Bottom Right Low",
        setup.rightBottomTradeDate,
        formatCurrency(setup.rightBottomLow, locale),
        ...sharedParts,
      ].join(" · "),
    },
  ];

  if (setup.necklineTradeDate && setup.necklinePrice != null) {
    markers.push({
      key: `neckline-${setup.necklineTradeDate}`,
      label: isZh ? "颈线" : "Neckline",
      date: setup.necklineTradeDate,
      price: setup.necklinePrice,
      tone: "neckline",
      description: [
        isZh ? "双底颈线" : "Double-Bottom Neckline",
        setup.necklineTradeDate,
        formatCurrency(setup.necklinePrice, locale),
        setup.bottomDistancePct != null
          ? `${isZh ? "底部价差" : "Bottom Spread"} ${formatPercent(setup.bottomDistancePct, 2)}`
          : null,
      ]
        .filter(Boolean)
        .join(" · "),
    });
  }

  if (setup.breakoutTradeDate) {
    markers.push({
      key: `breakout-${setup.breakoutTradeDate}`,
      label: isZh ? "突破" : "Breakout",
      date: setup.breakoutTradeDate,
      price: setup.breakoutClose ?? setup.necklinePrice,
      tone: "breakout",
      description: [
        isZh ? "双底突破确认" : "Double-Bottom Breakout",
        setup.breakoutTradeDate,
        setup.breakoutClose != null ? formatCurrency(setup.breakoutClose, locale) : null,
        setup.breakoutWaitBars != null
          ? `${isZh ? "右底后" : "After Right Bottom"} ${Math.round(setup.breakoutWaitBars)} ${isZh ? "根K线" : "bars"}`
          : null,
        setup.breakoutVolumeRatio != null
          ? `${isZh ? "放量倍数" : "Volume Ratio"} ${setup.breakoutVolumeRatio.toFixed(2)}x`
          : null,
      ]
        .filter(Boolean)
        .join(" · "),
    });
  }

  return markers;
}

function buildLifecycleGapOverlays(
  bars: CandleBarOut[],
  setup: IslandReversalGapSetup | null,
  locale: string,
  isZh: boolean
): LifecycleGapOverlay[] {
  if (!setup || bars.length === 0) {
    return [];
  }

  const overlays: LifecycleGapOverlay[] = [];
  const leftGapIndex = bars.findIndex((bar) => bar.trade_date === setup.leftGapTradeDate);

  if (leftGapIndex > 0) {
    const previousBar = bars[leftGapIndex - 1];
    const leftGapBar = bars[leftGapIndex];
    const lowPrice = leftGapBar.high;
    const highPrice = previousBar.low;

    if (highPrice > lowPrice) {
      overlays.push({
        key: `left-gap-${setup.leftGapTradeDate}`,
        label: isZh ? "左缺口" : "Left Gap",
        referenceDate: previousBar.trade_date,
        anchorDate: leftGapBar.trade_date,
        lowPrice,
        highPrice,
        tone: "left_gap",
        description: [
          isZh ? "左侧向下缺口" : "Left Gap Down",
          `${previousBar.trade_date} -> ${leftGapBar.trade_date}`,
          `${formatCurrency(lowPrice, locale)} - ${formatCurrency(highPrice, locale)}`,
          setup.leftGapPct != null ? formatPercent(setup.leftGapPct, 2) : null,
        ]
          .filter(Boolean)
          .join(" · "),
      });
    }
  }

  const breakoutIndex = bars.findIndex((bar) => bar.trade_date === setup.breakoutTradeDate);
  if (breakoutIndex > 0) {
    const previousBar = bars[breakoutIndex - 1];
    const breakoutBar = bars[breakoutIndex];
    const lowPrice = previousBar.high;
    const highPrice = breakoutBar.low;

    if (highPrice > lowPrice) {
      overlays.push({
        key: `right-gap-${setup.breakoutTradeDate}`,
        label: isZh ? "右缺口" : "Right Gap",
        referenceDate: previousBar.trade_date,
        anchorDate: breakoutBar.trade_date,
        lowPrice,
        highPrice,
        tone: "right_gap",
        description: [
          isZh ? "右侧向上缺口" : "Right Gap Up",
          `${previousBar.trade_date} -> ${breakoutBar.trade_date}`,
          `${formatCurrency(lowPrice, locale)} - ${formatCurrency(highPrice, locale)}`,
          setup.breakoutGapPct != null ? formatPercent(setup.breakoutGapPct, 2) : null,
        ]
          .filter(Boolean)
          .join(" · "),
      });
    }
  }

  return overlays;
}

function LifecycleDetailPanel({
  runId,
  row,
  signals,
}: {
  runId: string;
  row: PositionLifecycleRow;
  signals: BacktestSignalOut[];
}) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const resolvedEntryTradeDate = row.entryTradeDate || toTradeDateKey(row.entryTs);
  const initialEntrySignal = findLifecycleSignal(signals, row, "BUY");
  const initialDoubleBottomSetup = extractDoubleBottomSetup(initialEntrySignal);
  const initialLookbackDays = (() => {
    if (!initialDoubleBottomSetup || !resolvedEntryTradeDate) {
      return LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS;
    }

    const leftBottomTime = Date.parse(`${initialDoubleBottomSetup.leftBottomTradeDate}T00:00:00Z`);
    const entryTime = Date.parse(`${resolvedEntryTradeDate}T00:00:00Z`);
    if (!Number.isFinite(leftBottomTime) || !Number.isFinite(entryTime) || entryTime <= leftBottomTime) {
      return LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS;
    }

    const calendarDaySpan = Math.ceil((entryTime - leftBottomTime) / 86_400_000) + 5;
    return normalizeLifecycleLookbackDays(
      Math.max(LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS, calendarDaySpan)
    );
  })();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<CandleSeriesOut | null>(null);
  const [supportResistanceDetail, setSupportResistanceDetail] = useState<SupportResistanceBacktestOut | null>(null);
  const [supportResistanceError, setSupportResistanceError] = useState<string | null>(null);
  const [lookbackTradingDays, setLookbackTradingDays] = useState(
    initialLookbackDays
  );
  const [lookbackInput, setLookbackInput] = useState(
    String(initialLookbackDays)
  );
  const [postExitTradingDays, setPostExitTradingDays] = useState(
    LIFECYCLE_POST_EXIT_LOOKAHEAD_DAYS
  );
  const [postExitInput, setPostExitInput] = useState(
    String(LIFECYCLE_POST_EXIT_LOOKAHEAD_DAYS)
  );
  const entryTradeDate = row.entryTradeDate || toTradeDateKey(row.entryTs);
  const exitTradeDate = row.exitTradeDate || null;
  const fetchBufferDays = estimateLifecycleFetchBufferDays(lookbackTradingDays);
  const fetchStartDate =
    shiftDateKey(entryTradeDate, -fetchBufferDays) || entryTradeDate;
  const baseEndDate =
    row.exitTradeDate || row.markTradeDate || entryTradeDate || fetchStartDate;
  const fetchForwardBufferDays =
    row.status === "closed" && postExitTradingDays > 0
      ? estimateLifecycleFetchBufferDays(postExitTradingDays)
      : 0;
  const fetchEndDate =
    row.status === "closed" && exitTradeDate && fetchForwardBufferDays > 0
      ? shiftDateKey(exitTradeDate, fetchForwardBufferDays) || baseEndDate
      : baseEndDate;
  const entrySignal = useMemo(() => findLifecycleSignal(signals, row, "BUY"), [row, signals]);
  const exitSignal = useMemo(() => findLifecycleSignal(signals, row, "SELL"), [row, signals]);
  const doubleBottomSetup = useMemo(
    () => extractDoubleBottomSetup(entrySignal) || extractDoubleBottomSetup(exitSignal),
    [entrySignal, exitSignal]
  );
  const supportResistanceSetup = useMemo(
    () => extractSupportResistanceSetup(entrySignal) || extractSupportResistanceSetup(exitSignal),
    [entrySignal, exitSignal]
  );
  const markers = useMemo(
    () => [
      ...buildLifecycleChartMarkers(row, locale, isZh),
      ...buildDoubleBottomSetupMarkers(doubleBottomSetup, locale, isZh),
      ...buildSupportResistanceMarkers(supportResistanceDetail?.events || [], locale, isZh),
    ],
    [doubleBottomSetup, isZh, locale, row, supportResistanceDetail?.events]
  );

  function applyLookbackDays(nextValue: number) {
    const normalized = normalizeLifecycleLookbackDays(nextValue);
    setLookbackTradingDays(normalized);
    setLookbackInput(String(normalized));
  }

  function applyPostExitDays(nextValue: number) {
    const normalized = normalizeLifecycleLookbackDays(nextValue);
    setPostExitTradingDays(normalized);
    setPostExitInput(String(normalized));
  }

  useEffect(() => {
    if (!fetchStartDate || !fetchEndDate) {
      setError(isZh ? "缺少生命周期对应的交易日期，暂时无法绘图" : "Lifecycle dates are missing, so the chart cannot be drawn yet.");
      setSeries(null);
      setLoading(false);
      return;
    }
    if (fetchStartDate > fetchEndDate) {
      setError(isZh ? "生命周期日期区间无效" : "The lifecycle date range is invalid.");
      setSeries(null);
      setLoading(false);
      return;
    }

    let active = true;
    if (
      series
      && series.adjusted
      && series.symbol.toUpperCase() === row.symbol.toUpperCase()
      && series.start_date <= fetchStartDate
      && series.end_date >= fetchEndDate
    ) {
      setLoading(false);
      setError(null);
      return () => {
        active = false;
      };
    }

    setLoading(true);
    setError(null);

    void getCandleSeries({
      symbol: row.symbol,
      start_date: fetchStartDate,
      end_date: fetchEndDate,
      adjusted: true,
    })
      .then((nextSeries) => {
        if (!active) {
          return;
        }
        setSeries(nextSeries);
      })
      .catch((err) => {
        if (!active) {
          return;
        }
        setSeries(null);
        setError(err instanceof Error ? err.message : isZh ? "加载生命周期蜡烛图失败" : "Failed to load the lifecycle candlestick chart.");
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [fetchEndDate, fetchStartDate, isZh, row.symbol, series]);

  const bars = useMemo(
    () =>
      trimLifecycleBars(
        series?.bars ?? [],
        entryTradeDate,
        baseEndDate,
        lookbackTradingDays,
        row.status === "closed" ? postExitTradingDays : 0
      ),
    [baseEndDate, entryTradeDate, lookbackTradingDays, postExitTradingDays, row.status, series?.bars]
  );
  const visibleStartDate = bars[0]?.trade_date || fetchStartDate;
  const visibleEndDate = bars[bars.length - 1]?.trade_date || baseEndDate;
  useEffect(() => {
    if (!supportResistanceSetup || !visibleStartDate || !visibleEndDate || bars.length === 0) {
      setSupportResistanceDetail(null);
      setSupportResistanceError(null);
      return;
    }
    let active = true;
    setSupportResistanceError(null);
    void getBacktestSupportResistance(runId, {
      symbol: row.symbol,
      start_date: visibleStartDate,
      end_date: visibleEndDate,
    })
      .then((detail) => {
        if (active) setSupportResistanceDetail(detail);
      })
      .catch((err) => {
        if (!active) return;
        setSupportResistanceDetail(null);
        setSupportResistanceError(err instanceof Error ? err.message : (isZh ? "加载支撑压力审计数据失败" : "Failed to load support/resistance audit data"));
      });
    return () => { active = false; };
  }, [bars.length, isZh, row.symbol, runId, supportResistanceSetup, visibleEndDate, visibleStartDate]);
  const gapSetup = useMemo(
    () => extractIslandReversalGapSetup(entrySignal) || extractIslandReversalGapSetup(exitSignal),
    [entrySignal, exitSignal]
  );
  const requiredDoubleBottomLookback = useMemo(() => {
    if (!doubleBottomSetup || !entryTradeDate || !series?.bars?.length) {
      return null;
    }

    const leftBottomIndex = series.bars.findIndex(
      (bar) => bar.trade_date === doubleBottomSetup.leftBottomTradeDate
    );
    const entryIndex = series.bars.findIndex(
      (bar) => bar.trade_date >= entryTradeDate
    );
    if (leftBottomIndex < 0 || entryIndex < 0 || entryIndex <= leftBottomIndex) {
      return null;
    }

    return normalizeLifecycleLookbackDays(
      Math.max(LIFECYCLE_PRE_ENTRY_LOOKBACK_TRADING_DAYS, entryIndex - leftBottomIndex + 3)
    );
  }, [doubleBottomSetup, entryTradeDate, series?.bars]);
  const gapOverlays = useMemo(
    () => buildLifecycleGapOverlays(bars, gapSetup, locale, isZh),
    [bars, gapSetup, isZh, locale]
  );
  const zoneOverlays = useMemo<LifecycleZoneOverlay[]>(() => {
    if (!supportResistanceSetup) return [];
    const versions = supportResistanceDetail?.zone_versions || [];
    const availableDates = new Set(bars.map((bar) => bar.trade_date));
    return versions
      .filter((version) => version.status === "active" && version.geometry)
      .filter((version) => {
        const geometry = version.geometry!;
        return geometry.end_date >= (visibleStartDate || "")
          && geometry.start_date <= (visibleEndDate || "")
          && availableDates.has(geometry.start_date)
          && availableDates.has(geometry.end_date);
      })
      .map((version) => {
        const geometry = version.geometry!;
        const normalizedSlope = version.atr_width > 0
          ? geometry.slope_per_session / version.atr_width
          : null;
        const direction = geometry.slope_per_session > 0
          ? (isZh ? "上倾" : "Rising")
          : geometry.slope_per_session < 0
            ? (isZh ? "下倾" : "Falling")
            : (isZh ? "水平" : "Flat");
        return {
        key: `sr-zone-${version.id}`,
        startDate: geometry.start_date,
        endDate: geometry.end_date,
        startCenterPrice: geometry.start_center_price,
        startLowerPrice: geometry.start_lower_price,
        startUpperPrice: geometry.start_upper_price,
        endCenterPrice: geometry.end_center_price,
        endLowerPrice: geometry.end_lower_price,
        endUpperPrice: geometry.end_upper_price,
        slopePerSession: geometry.slope_per_session,
        slopeAtrPerSession: normalizedSlope,
        role: version.role,
        description: [
          version.role === "support" ? (isZh ? "支撑区" : "Support Zone") : (isZh ? "压力区" : "Resistance Zone"),
          `${formatCurrency(geometry.end_lower_price, locale)} - ${formatCurrency(geometry.end_upper_price, locale)}`,
          direction,
          normalizedSlope == null ? null : `${normalizedSlope.toFixed(3)} ATR/${isZh ? "日" : "session"}`,
        ].filter(Boolean).join(" · "),
      };
    });
  }, [bars, isZh, locale, supportResistanceDetail?.zone_versions, supportResistanceSetup, visibleEndDate, visibleStartDate]);
  const zoneLegendItems = useMemo(
    () => currentZoneOverlays(zoneOverlays, visibleEndDate),
    [visibleEndDate, zoneOverlays],
  );

  useEffect(() => {
    if (
      requiredDoubleBottomLookback == null
      || lookbackTradingDays !== initialLookbackDays
      || requiredDoubleBottomLookback <= lookbackTradingDays
    ) {
      return;
    }

    applyLookbackDays(requiredDoubleBottomLookback);
  }, [initialLookbackDays, lookbackTradingDays, requiredDoubleBottomLookback]);

  return (
    <div style={lifecycleDetailPanelStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "flex-start",
          marginBottom: 14,
        }}
      >
        <div>
          <div style={{ color: "#f8fafc", fontSize: 17, fontWeight: 800, marginBottom: 6 }}>
            {row.symbol} #{row.sequence} · {isZh ? "生命周期图" : "Lifecycle Chart"}
          </div>
          <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
            {isZh
              ? `${visibleStartDate || "-"} -> ${visibleEndDate || "-"}，当前额外包含买入前 ${lookbackTradingDays} 个交易日${
                  row.status === "closed" ? `、卖出后 ${postExitTradingDays} 个交易日` : ""
                }的走势，并标出买卖信号、实际买卖，以及形态关键位置，例如岛形反转缺口或双底的左右底、颈线与突破日。`
              : `${visibleStartDate || "-"} -> ${visibleEndDate || "-"}, currently including ${lookbackTradingDays} trading days before entry${
                  row.status === "closed" ? ` and ${postExitTradingDays} trading days after exit` : ""
                } so you can see signal-generation points, actual fills, and key pattern landmarks such as island-reversal gaps or a double bottom's two lows, neckline, and breakout day.`}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone={row.status === "closed" ? "success" : "warning"}>
            {row.status === "closed"
              ? isZh
                ? "已平仓"
                : "Closed"
              : isZh
                ? "持有中"
                : "Open"}
          </Badge>
          <Badge tone="neutral">
            {isZh ? "股数" : "Qty"} {row.qty.toLocaleString(locale, { maximumFractionDigits: 4 })}
          </Badge>
          <Badge tone="info">
            {isZh ? "收益率" : "Return"} {formatPercent(row.returnPct, 2)}
          </Badge>
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 12,
          marginBottom: 14,
          padding: "12px 14px",
          borderRadius: 16,
          background: "rgba(15, 23, 42, 0.56)",
          border: "1px solid rgba(71, 85, 105, 0.24)",
        }}
      >
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ color: "rgba(226, 232, 240, 0.88)", fontSize: 13, fontWeight: 700 }}>
            {isZh ? "买入前显示范围" : "Pre-entry Window"}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {LIFECYCLE_PRE_ENTRY_LOOKBACK_PRESETS.map((days) => (
              <button
                key={days}
                type="button"
                onClick={() => applyLookbackDays(days)}
                style={lifecycleLookbackChipStyle(lookbackTradingDays === days)}
              >
                {days}
                {isZh ? " 日" : "d"}
              </button>
            ))}
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                color: "rgba(226, 232, 240, 0.78)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              <span>{isZh ? "自定义" : "Custom"}</span>
              <input
                type="number"
                min={LIFECYCLE_PRE_ENTRY_LOOKBACK_MIN}
                max={LIFECYCLE_PRE_ENTRY_LOOKBACK_MAX}
                inputMode="numeric"
                value={lookbackInput}
                onChange={(event) => setLookbackInput(event.target.value)}
                onBlur={() =>
                  applyLookbackDays(
                    lookbackInput.trim() === "" ? lookbackTradingDays : Number(lookbackInput)
                  )
                }
                onKeyDown={(event) => {
                  if (event.key !== "Enter") {
                    return;
                  }
                  event.preventDefault();
                  applyLookbackDays(
                    lookbackInput.trim() === "" ? lookbackTradingDays : Number(lookbackInput)
                  );
                }}
                style={lifecycleLookbackInputStyle}
              />
              <span style={{ color: "rgba(148, 163, 184, 0.82)" }}>
                {isZh ? "交易日" : "days"}
              </span>
            </label>
          </div>
        </div>
        <div style={{ display: "grid", gap: 10 }}>
          <div style={{ color: "rgba(226, 232, 240, 0.88)", fontSize: 13, fontWeight: 700 }}>
            {isZh ? "卖出后显示范围" : "Post-exit Window"}
          </div>
          {row.status === "closed" ? (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              {LIFECYCLE_POST_EXIT_LOOKAHEAD_PRESETS.map((days) => (
                <button
                  key={days}
                  type="button"
                  onClick={() => applyPostExitDays(days)}
                  style={lifecycleLookbackChipStyle(postExitTradingDays === days)}
                >
                  {days}
                  {isZh ? " 日" : "d"}
                </button>
              ))}
              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  color: "rgba(226, 232, 240, 0.78)",
                  fontSize: 13,
                  fontWeight: 600,
                }}
              >
                <span>{isZh ? "自定义" : "Custom"}</span>
                <input
                  type="number"
                  min={LIFECYCLE_PRE_ENTRY_LOOKBACK_MIN}
                  max={LIFECYCLE_PRE_ENTRY_LOOKBACK_MAX}
                  inputMode="numeric"
                  value={postExitInput}
                  onChange={(event) => setPostExitInput(event.target.value)}
                  onBlur={() =>
                    applyPostExitDays(
                      postExitInput.trim() === "" ? postExitTradingDays : Number(postExitInput)
                    )
                  }
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") {
                      return;
                    }
                    event.preventDefault();
                    applyPostExitDays(
                      postExitInput.trim() === "" ? postExitTradingDays : Number(postExitInput)
                    );
                  }}
                  style={lifecycleLookbackInputStyle}
                />
                <span style={{ color: "rgba(148, 163, 184, 0.82)" }}>
                  {isZh ? "交易日" : "days"}
                </span>
              </label>
            </div>
          ) : (
            <div style={{ color: "rgba(148, 163, 184, 0.82)", fontSize: 13, lineHeight: 1.6 }}>
              {isZh ? "这段生命周期还没有卖出，所以暂时没有卖出后范围。" : "This lifecycle is still open, so there is no post-exit window yet."}
            </div>
          )}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "买入交易日" : "Buy Trade Date"}</div>
          <div style={miniMetricValueStyle}>
            {formatLifecycleEventMoment(row.entryTs, row.entryTradeDate, locale)}
          </div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "卖出 / 标记交易日" : "Sell / Mark Trade Date"}</div>
          <div style={miniMetricValueStyle}>
            {formatLifecycleEventMoment(row.exitTs || row.markTs, row.exitTradeDate || row.markTradeDate, locale)}
          </div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>{isZh ? "买入价" : "Buy Price"}</div>
          <div style={miniMetricValueStyle}>{formatCurrency(row.entryPrice, locale)}</div>
        </div>
        <div style={miniMetricStyle}>
          <div style={labelStyle}>
            {row.status === "closed"
              ? isZh
                ? "卖出价"
                : "Sell Price"
              : isZh
                ? "标记价"
                : "Mark Price"}
          </div>
          <div style={miniMetricValueStyle}>
            {formatCurrency(row.exitPrice ?? row.markPrice, locale)}
          </div>
        </div>
      </div>

      {loading ? (
        <div style={emptyStateStyle}>{isZh ? "正在加载这段生命周期的蜡烛图..." : "Loading the candlestick chart for this lifecycle..."}</div>
      ) : error ? (
        <div style={{ ...emptyStateStyle, color: "#fecaca", border: "1px solid rgba(248, 113, 113, 0.22)" }}>{error}</div>
      ) : bars.length === 0 ? (
        <div style={emptyStateStyle}>
          {isZh ? "这个生命周期区间内没有可用的日线数据" : "There are no daily bars available inside this lifecycle window"}
        </div>
      ) : (
        <>
          <CandlestickLightweightChart
            bars={bars}
            markers={markers as ChartOverlayMarker[]}
            gaps={gapOverlays as ChartGapOverlay[]}
            zones={zoneOverlays as ChartZoneOverlay[]}
            locale={locale}
            showVolume
            height={384}
            ariaLabel={isZh ? "生命周期蜡烛图" : "Lifecycle candlestick chart"}
          />
          <div
            style={{
              display: "flex",
              gap: 14,
              flexWrap: "wrap",
              marginTop: 10,
              color: "rgba(226, 232, 240, 0.82)",
              fontSize: 13,
              fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            }}
          >
            {zoneLegendItems.map((zone) => (
              <span key={zone.key} style={legendItemStyle} title={zone.description}>
                <span style={{ ...legendDotStyle, background: zone.role === "support" ? "#22c55e" : "#ef4444" }} />
                {zone.description}
              </span>
            ))}
            {gapOverlays.map((overlay) => (
              <span key={overlay.key} style={legendItemStyle}>
                <span
                  style={{
                    ...legendDotStyle,
                    background: overlay.tone === "left_gap" ? "#f59e0b" : "#06b6d4",
                  }}
                />
                {overlay.description}
              </span>
            ))}
            {markers.map((marker) => (
              <span key={marker.key} style={legendItemStyle}>
                <span
                  style={{
                    ...legendDotStyle,
                    background:
                      marker.tone === "buy"
                        ? "#22c55e"
                        : marker.tone === "buy_signal"
                          ? "#38bdf8"
                        : marker.tone === "sell"
                          ? "#ef4444"
                          : marker.tone === "sell_signal"
                            ? "#f59e0b"
                            : marker.tone === "neckline"
                              ? "#94a3b8"
                              : marker.tone === "breakout"
                                ? "#f97316"
                            : marker.tone === "left_bottom"
                              ? "#eab308"
                              : marker.tone === "right_bottom"
                                ? "#14b8a6"
                            : "#38bdf8",
                  }}
                />
                {marker.description}
              </span>
            ))}
          </div>
          {supportResistanceError ? (
            <div style={{ marginTop: 8, color: "#fbbf24", fontSize: 12 }}>
              {supportResistanceError}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function PositionLifecycleCard({
  run,
  transactions,
  totalTransactions,
}: {
  run: BacktestDetailOut;
  transactions: BacktestTransactionOut[];
  totalTransactions: number;
}) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [visibleRowCount, setVisibleRowCount] = useState(LIFECYCLE_ROW_BATCH_SIZE);
  const [transactionLimit, setTransactionLimit] = useState(INITIAL_LIFECYCLE_TRANSACTION_LIMIT);
  const [expandedRowKey, setExpandedRowKey] = useState<string | null>(null);
  const [pnlFilter, setPnlFilter] = useState<PositionLifecyclePnlFilter>("all");
  const [sortKey, setSortKey] = useState<PositionLifecycleSortKey>("endTime");
  const [sortDirection, setSortDirection] = useState<PositionLifecycleSortDirection>("desc");
  const scopedTransactions = useMemo(
    () => transactions.slice(0, transactionLimit),
    [transactionLimit, transactions],
  );
  const scopedRun = useMemo(
    () => ({ ...run, transactions: scopedTransactions }),
    [run, scopedTransactions],
  );
  const rows = useMemo(() => buildPositionLifecycleRows(scopedRun), [scopedRun]);

  useEffect(() => {
    setVisibleRowCount(LIFECYCLE_ROW_BATCH_SIZE);
    setTransactionLimit(INITIAL_LIFECYCLE_TRANSACTION_LIMIT);
    setExpandedRowKey(null);
  }, [run.id]);

  const closedRows = rows.filter((row) => row.status === "closed");
  const openRows = rows.filter((row) => row.status === "open");
  const winningClosedRows = closedRows.filter((row) => (row.pnl ?? 0) > 0);
  const rowsWithHoldingDays = rows.filter((row) => row.holdingDays != null);
  const totalPnl = rows.reduce((sum, row) => sum + (row.pnl ?? 0), 0);
  const averageHoldingDays =
    rowsWithHoldingDays.length > 0
      ? rowsWithHoldingDays.reduce((sum, row) => sum + (row.holdingDays ?? 0), 0) / rowsWithHoldingDays.length
      : null;
  const winRate =
    closedRows.length > 0 ? winningClosedRows.length / closedRows.length : null;
  const pnlFilterCounts = useMemo(
    () => ({
      profit: rows.filter((row) => classifyLifecyclePnl(row) === "profit").length,
      loss: rows.filter((row) => classifyLifecyclePnl(row) === "loss").length,
      flat: rows.filter((row) => classifyLifecyclePnl(row) === "flat").length,
    }),
    [rows]
  );
  const filteredRows = useMemo(() => {
    if (pnlFilter === "all") {
      return rows;
    }
    return rows.filter((row) => classifyLifecyclePnl(row) === pnlFilter);
  }, [pnlFilter, rows]);
  const sortedRows = useMemo(
    () => [...filteredRows].sort((left, right) => comparePositionLifecycleRows(left, right, sortKey, sortDirection)),
    [filteredRows, sortDirection, sortKey]
  );
  const visibleRows = sortedRows.slice(0, visibleRowCount);
  const canLoadMore =
    visibleRowCount < sortedRows.length
    || scopedTransactions.length < totalTransactions;

  const loadMoreRows = () => {
    const nextRowCount = visibleRowCount + LIFECYCLE_ROW_BATCH_SIZE;
    setVisibleRowCount(nextRowCount);
    if (nextRowCount > sortedRows.length && scopedTransactions.length < totalTransactions) {
      setTransactionLimit((current) => nextVisibleItemCount(
        current,
        totalTransactions,
        LIFECYCLE_TRANSACTION_BATCH_SIZE,
      ));
    }
  };

  const lifecycleSortLabels: Record<PositionLifecycleSortKey, string> = {
    symbol: isZh ? "标的" : "symbol",
    entryTime: isZh ? "入场时间" : "entry time",
    endTime: isZh ? "出场 / 标记时间" : "exit / mark time",
    qty: isZh ? "股数" : "quantity",
    entryPrice: isZh ? "入场价" : "entry price",
    endPrice: isZh ? "出场 / 标记价" : "exit / mark price",
    pnl: isZh ? "盈亏" : "PnL",
    returnPct: isZh ? "收益率" : "return",
    holdingDays: isZh ? "持有天数" : "days held",
  };

  const lifecycleColumns: Array<{
    key: string;
    label: string;
    sortKey?: PositionLifecycleSortKey;
  }> = [
    { key: "symbol", label: isZh ? "标的" : "Symbol", sortKey: "symbol" },
    { key: "sequence", label: isZh ? "周期" : "Cycle" },
    { key: "status", label: isZh ? "状态" : "Status" },
    { key: "entryTime", label: isZh ? "入场时间" : "Entry Time", sortKey: "entryTime" },
    { key: "endTime", label: isZh ? "出场 / 标记时间" : "Exit / Mark Time", sortKey: "endTime" },
    { key: "qty", label: isZh ? "股数" : "Qty", sortKey: "qty" },
    { key: "entryPrice", label: isZh ? "入场价" : "Entry Px", sortKey: "entryPrice" },
    { key: "endPrice", label: isZh ? "出场 / 标记价" : "Exit / Mark Px", sortKey: "endPrice" },
    { key: "pnl", label: isZh ? "盈亏" : "PnL", sortKey: "pnl" },
    { key: "returnPct", label: isZh ? "收益率" : "Return", sortKey: "returnPct" },
    { key: "holdingDays", label: isZh ? "持有天数" : "Days Held", sortKey: "holdingDays" },
    { key: "entryReason", label: isZh ? "入场原因" : "Entry Reason" },
    { key: "exitReason", label: isZh ? "出场原因" : "Exit Reason" },
  ];

  const toggleSort = (nextSortKey: PositionLifecycleSortKey) => {
    if (sortKey === nextSortKey) {
      setSortDirection((current) => (current === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(nextSortKey);
    setSortDirection(defaultLifecycleSortDirection(nextSortKey));
  };

  useEffect(() => {
    if (!expandedRowKey) {
      return;
    }
    if (!filteredRows.some((row) => row.key === expandedRowKey)) {
      setExpandedRowKey(null);
    }
  }, [expandedRowKey, filteredRows]);

  const pnlFilterOptions: Array<{
    value: PositionLifecyclePnlFilter;
    label: string;
    count: number;
  }> = [
    {
      value: "all",
      label: isZh ? "全部" : "All",
      count: rows.length,
    },
    {
      value: "profit",
      label: isZh ? "盈利" : "Profit",
      count: pnlFilterCounts.profit,
    },
    {
      value: "loss",
      label: isZh ? "亏损" : "Loss",
      count: pnlFilterCounts.loss,
    },
    {
      value: "flat",
      label: isZh ? "持平" : "Flat",
      count: pnlFilterCounts.flat,
    },
  ];

  return (
    <section style={sectionCardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>
            {isZh ? "持仓生命周期" : "Position Lifecycles"}
          </h2>
          <p style={sectionSubtitleStyle}>
            {isZh
              ? "每一行表示一段从买入到卖出的 round-trip；如果回测结束时仍持有，则按最后快照价格标记为 open。"
              : "Each row represents one buy-to-sell round-trip. Positions still open at the end of the run are shown as open and marked to the latest snapshot price."}
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Badge tone="info">
            {rows.length} {isZh ? "段生命周期" : rows.length === 1 ? "lifecycle" : "lifecycles"}
          </Badge>
          {canLoadMore ? (
            <button
              type="button"
              onClick={loadMoreRows}
              style={tableToggleButtonStyle}
            >
              {isZh ? "再显示 12 段" : "Show 12 More"}
            </button>
          ) : null}
        </div>
      </div>

          {rows.length === 0 ? (
        <div style={emptyStateStyle}>
          {isZh
            ? "这次回测还没有形成可识别的持仓生命周期"
            : "This backtest does not have identifiable position lifecycles yet"}
        </div>
      ) : filteredRows.length === 0 ? (
        <div style={emptyStateStyle}>
          {isZh ? "当前筛选条件下没有匹配的生命周期" : "No lifecycles match the current filter."}
        </div>
      ) : (
        <>
          <div
            style={{
              marginBottom: 14,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
              gap: 12,
            }}
          >
            <div style={miniMetricStyle}>
              <div style={labelStyle}>{isZh ? "已闭环" : "Closed"}</div>
              <div style={miniMetricValueStyle}>{closedRows.length}</div>
            </div>
            <div style={miniMetricStyle}>
              <div style={labelStyle}>{isZh ? "仍持有" : "Open"}</div>
              <div style={miniMetricValueStyle}>{openRows.length}</div>
            </div>
            <div style={miniMetricStyle}>
              <div style={labelStyle}>{isZh ? "胜率" : "Win Rate"}</div>
              <div style={miniMetricValueStyle}>{formatPercent(winRate, 2)}</div>
            </div>
            <div style={miniMetricStyle}>
              <div style={labelStyle}>{isZh ? "平均持有天数" : "Avg Hold Days"}</div>
              <div style={miniMetricValueStyle}>
                {averageHoldingDays == null ? "-" : averageHoldingDays.toLocaleString(locale, { maximumFractionDigits: 1 })}
              </div>
            </div>
              <div style={miniMetricStyle}>
                <div style={labelStyle}>{isZh ? "生命周期盈亏" : "Lifecycle PnL"}</div>
                <div style={miniMetricValueStyle}>{formatCurrency(totalPnl, locale)}</div>
              </div>
            </div>

          <div
            style={{
              marginBottom: 12,
              display: "flex",
              justifyContent: "space-between",
              gap: 12,
              flexWrap: "wrap",
              alignItems: "center",
            }}
          >
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              {pnlFilterOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setPnlFilter(option.value)}
                  style={lifecyclePnlFilterChipStyle(pnlFilter === option.value)}
                >
                  {option.label} {option.count}
                </button>
              ))}
            </div>
            <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
              {isZh
                ? `当前显示 ${visibleRows.length} / ${filteredRows.length} 段生命周期${
                    pnlFilter === "all" ? "" : `（总计 ${rows.length} 段）`
                  }；基于最近 ${scopedTransactions.length} / ${totalTransactions} 笔成交，按${lifecycleSortLabels[sortKey]}${sortDirection === "desc" ? "降序" : "升序"}排列，可点击表头切换。`
                : `Showing ${visibleRows.length} / ${filteredRows.length} lifecycles${
                    pnlFilter === "all" ? "" : ` (${rows.length} total)`
                  }, based on the latest ${scopedTransactions.length} / ${totalTransactions} transactions and sorted by ${lifecycleSortLabels[sortKey]} in ${
                    sortDirection === "desc" ? "descending" : "ascending"
                  } order. Click a sortable header to reorder.`}
            </div>
          </div>

          <div
            style={{
              overflowX: "auto",
              borderRadius: 18,
              border: "1px solid rgba(71, 85, 105, 0.28)",
              background: "rgba(15, 23, 42, 0.74)",
            }}
          >
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                minWidth: 1440,
                fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              <thead>
                <tr
                  style={{
                    background: "rgba(30, 41, 59, 0.9)",
                    color: "rgba(148, 163, 184, 0.88)",
                    textAlign: "left",
                  }}
                >
                  {lifecycleColumns.map((column) => (
                    <th
                      key={column.key}
                      aria-sort={
                        column.sortKey
                          ? sortKey === column.sortKey
                            ? sortDirection === "asc"
                              ? "ascending"
                              : "descending"
                            : "none"
                          : undefined
                      }
                      style={{
                        ...stickyTableHeaderCellStyle,
                        padding: "12px 14px",
                        fontSize: 12,
                        fontWeight: 700,
                        letterSpacing: "0.03em",
                        textTransform: "uppercase",
                      }}
                    >
                      {column.sortKey ? (
                        <button
                          type="button"
                          onClick={() => toggleSort(column.sortKey!)}
                          style={lifecycleSortButtonStyle(sortKey === column.sortKey)}
                        >
                          <span>{column.label}</span>
                          <span style={lifecycleSortIndicatorStyle(sortKey === column.sortKey)}>
                            {sortKey === column.sortKey ? (sortDirection === "desc" ? "↓" : "↑") : "↕"}
                          </span>
                        </button>
                      ) : (
                        column.label
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const displayEndTs = getLifecycleDisplayEndTs(row);
                  const displayEndPrice = getLifecycleDisplayEndPrice(row);
                  const pnlTone = (row.pnl ?? 0) >= 0 ? "#15803d" : "#b91c1c";
                  const statusLabel =
                    row.status === "closed"
                      ? isZh
                        ? "平仓"
                        : "Closed"
                      : isZh
                        ? "持有"
                        : "Open";
                  const expanded = expandedRowKey === row.key;

                  return (
                    <Fragment key={row.key}>
                      <tr
                        tabIndex={0}
                        aria-expanded={expanded}
                        onClick={() =>
                          setExpandedRowKey((current) => (current === row.key ? null : row.key))
                        }
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") {
                            return;
                          }
                          event.preventDefault();
                          setExpandedRowKey((current) => (current === row.key ? null : row.key));
                        }}
                        style={{
                          borderBottom: expanded
                            ? "1px solid rgba(56, 189, 248, 0.2)"
                            : "1px solid rgba(241, 245, 249, 1)",
                          cursor: "pointer",
                          background: expanded ? "rgba(8, 47, 73, 0.16)" : "transparent",
                          outline: "none",
                        }}
                      >
                        <td style={cellStyle}>
                          <span style={lifecyclePrimaryCellStyle}>
                            <span style={lifecycleChevronStyle(expanded)}>{expanded ? "▾" : "▸"}</span>
                            <span>{row.symbol}</span>
                          </span>
                        </td>
                        <td style={cellStyle}>#{row.sequence}</td>
                        <td style={lifecycleStatusCellStyle}>
                          <Badge
                            tone={row.status === "closed" ? "success" : "warning"}
                            style={lifecycleStatusBadgeStyle}
                          >
                            {statusLabel}
                          </Badge>
                        </td>
                        <td style={cellStyle}>{formatDateTime(row.entryTs, locale)}</td>
                        <td style={cellStyle}>
                          {displayEndTs
                            ? formatDateTime(displayEndTs, locale)
                            : row.status === "open"
                              ? isZh
                                ? "仍在持有"
                                : "Still Open"
                              : "-"}
                        </td>
                        <td style={cellStyle}>{row.qty.toLocaleString(locale, { maximumFractionDigits: 4 })}</td>
                        <td style={cellStyle}>{formatCurrency(row.entryPrice, locale)}</td>
                        <td style={cellStyle}>{formatCurrency(displayEndPrice, locale)}</td>
                        <td style={{ ...cellStyle, color: row.pnl == null ? cellStyle.color : pnlTone, fontWeight: 700 }}>
                          {formatCurrency(row.pnl, locale)}
                        </td>
                        <td style={cellStyle}>{formatPercent(row.returnPct, 2)}</td>
                        <td style={cellStyle}>
                          {row.holdingDays == null
                            ? "-"
                            : isZh
                              ? `${row.holdingDays} 天`
                              : `${row.holdingDays} d`}
                        </td>
                        <td style={cellStyle}>{row.entryReason || "-"}</td>
                        <td style={cellStyle}>
                          {row.exitReason ||
                            (row.status === "open"
                              ? isZh
                                ? "按最后快照估值"
                                : "Marked to latest snapshot"
                              : "-")}
                        </td>
                      </tr>
                      {expanded ? (
                        <tr>
                          <td colSpan={13} style={lifecycleExpandedCellStyle}>
                            <LifecycleDetailPanel runId={run.id} row={row} signals={run.signals} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function TransactionsCard({
  runId,
  transactions,
  totalTransactions,
}: {
  runId: string;
  transactions: BacktestTransactionOut[];
  totalTransactions: number;
}) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [visibleTransactionCount, setVisibleTransactionCount] = useState(TRANSACTION_TABLE_BATCH_SIZE);
  const visibleTransactions = useMemo(
    () => transactions.slice(0, visibleTransactionCount),
    [transactions, visibleTransactionCount]
  );

  useEffect(() => {
    setVisibleTransactionCount(TRANSACTION_TABLE_BATCH_SIZE);
  }, [runId]);

  const transactionRows = useMemo<BacktestTransactionDenseRow[]>(() => visibleTransactions.map((txn) => ({
    id: txn.id,
    ts: txn.ts || null,
    side: txn.side,
    symbol: txn.symbol,
    qty: txn.qty,
    price: txn.price,
    fee: txn.fee ?? null,
    netCashFlow: getMetaNumber(txn.meta, "net_cash_flow"),
    signalTs: getMetaText(txn.meta, "signal_ts"),
    reason: getMetaText(txn.meta, "reason") || "-",
  })), [visibleTransactions]);

  return (
    <section style={sectionCardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <div>
          <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{isZh ? "交易明细" : "Transactions"}</h2>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Badge tone="info">
            {totalTransactions} {isZh ? "笔交易" : totalTransactions === 1 ? "trade" : "trades"}
          </Badge>
          {visibleTransactions.length < totalTransactions ? (
            <button
              type="button"
              onClick={() => setVisibleTransactionCount((current) => nextVisibleItemCount(
                current,
                totalTransactions,
                TRANSACTION_TABLE_BATCH_SIZE,
              ))}
              style={tableToggleButtonStyle}
            >
              {isZh ? "再显示 10 笔" : "Show 10 More"}
            </button>
          ) : null}
        </div>
      </div>

      {transactions.length === 0 ? (
        <div style={emptyStateStyle}>
          {isZh
            ? "这次 run 还没有写入任何交易记录"
            : "This run has not written any transaction records yet"}
        </div>
      ) : (
        <>
          {totalTransactions > TRANSACTION_TABLE_BATCH_SIZE ? (
            <div style={{ marginBottom: 10, color: "rgba(148, 163, 184, 0.88)", fontSize: 13 }}>
              {isZh
                ? `当前显示最新 ${visibleTransactions.length} / ${totalTransactions} 条交易记录。`
                : `Showing the latest ${visibleTransactions.length} / ${totalTransactions} transactions.`}
            </div>
          ) : null}
          <BacktestTransactionsDenseTable rows={transactionRows} />
        </>
      )}
    </section>
  );
}

export default function BacktestDetailPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const runId = Array.isArray(router.query.runId) ? router.query.runId[0] : router.query.runId;

  const [run, setRun] = useState<BacktestDetailOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [equityLoading, setEquityLoading] = useState(true);
  const [equityError, setEquityError] = useState<string | null>(null);
  const [signalsLoading, setSignalsLoading] = useState(true);
  const [signalsError, setSignalsError] = useState<string | null>(null);
  const [transactionsLoading, setTransactionsLoading] = useState(true);
  const [transactionsError, setTransactionsError] = useState<string | null>(null);
  const [workerStatus, setWorkerStatus] = useState<BacktestWorkerStatus | null>(null);
  const [workerStatusUnavailable, setWorkerStatusUnavailable] = useState(false);
  const [transactionCollection, setTransactionCollection] = useState<TransactionCollectionState>(
    EMPTY_TRANSACTION_COLLECTION,
  );
  const transactionLoadGenerationRef = useRef(0);

  const loadTransactionPages = useCallback(async (
    targetRunId: string,
    generation: number,
    resumeCursor: string | null,
    loadInitialPage: boolean,
  ) => {
    let nextCursor = resumeCursor;

    if (loadInitialPage) {
      setTransactionsLoading(true);
      setTransactionsError(null);
      setTransactionCollection((current) => ({
        ...current,
        nextCursor: null,
        loading: true,
        complete: false,
        error: null,
      }));
      try {
        const firstPage = await getBacktestTransactions(targetRunId, {
          limit: INITIAL_TRANSACTION_PAGE_SIZE,
        });
        if (transactionLoadGenerationRef.current !== generation) return;
        nextCursor = firstPage.next_cursor || null;
        setRun((current) => current ? {
          ...current,
          transactions: mergeBacktestTransactions(current.transactions, firstPage.items),
          transaction_count: firstPage.total,
        } : current);
        setTransactionCollection({
          total: firstPage.total,
          nextCursor,
          loading: Boolean(nextCursor),
          complete: !nextCursor,
          error: null,
        });
        setTransactionsLoading(false);
      } catch (cause) {
        if (transactionLoadGenerationRef.current !== generation) return;
        const message = cause instanceof Error ? cause.message : BACKTEST_TRANSACTIONS_LOAD_FAILED;
        setTransactionsError(message);
        setTransactionsLoading(false);
        setTransactionCollection((current) => ({
          ...current,
          nextCursor: null,
          loading: false,
          complete: false,
          error: message,
        }));
        return;
      }
    } else {
      setTransactionCollection((current) => ({
        ...current,
        loading: true,
        error: null,
      }));
    }

    if (!nextCursor) return;

    try {
      await streamBacktestTransactionPages({
        cursor: nextCursor,
        loadPage: async (cursor) => {
          if (transactionLoadGenerationRef.current !== generation) {
            throw new Error("Transaction loading was superseded by another run");
          }
          return getBacktestTransactions(targetRunId, {
            limit: TRANSACTION_TAIL_PAGE_SIZE,
            cursor,
          });
        },
        onPage: (page) => {
          if (transactionLoadGenerationRef.current !== generation) return;
          setRun((current) => current ? {
            ...current,
            transactions: mergeBacktestTransactions(current.transactions, page.items),
            transaction_count: page.total,
          } : current);
          setTransactionCollection({
            total: page.total,
            nextCursor: page.next_cursor || null,
            loading: Boolean(page.next_cursor),
            complete: !page.next_cursor,
            error: null,
          });
        },
      });
      if (transactionLoadGenerationRef.current !== generation) return;
      setTransactionCollection((current) => ({
        ...current,
        nextCursor: null,
        loading: false,
        complete: true,
        error: null,
      }));
    } catch (cause) {
      if (transactionLoadGenerationRef.current !== generation) return;
      const pageError = cause instanceof BacktestTransactionPageError ? cause : null;
      const message = cause instanceof Error ? cause.message : BACKTEST_TRANSACTIONS_LOAD_FAILED;
      setTransactionCollection((current) => ({
        ...current,
        nextCursor: pageError?.resumeCursor || nextCursor,
        loading: false,
        complete: false,
        error: message,
      }));
    }
  }, []);

  useEffect(() => {
    if (!router.isReady || !runId) {
      return;
    }

    let cancelled = false;
    let pollTimer: number | null = null;
    const transactionLoadGeneration = transactionLoadGenerationRef.current + 1;
    transactionLoadGenerationRef.current = transactionLoadGeneration;
    setLoading(true);
    setError(null);
    setRun(null);
    setEquityLoading(true);
    setEquityError(null);
    setSignalsLoading(true);
    setSignalsError(null);
    setTransactionsLoading(true);
    setTransactionsError(null);
    setWorkerStatus(null);
    setWorkerStatusUnavailable(false);
    setTransactionCollection(EMPTY_TRANSACTION_COLLECTION);

    const loadCompletedDetails = async () => {
      void loadTransactionPages(runId, transactionLoadGeneration, null, true);
      const [equityResult, signalsResult] = await Promise.allSettled([
        getBacktestEquity(runId),
        getBacktestSignals(runId, { limit: 100 }),
      ]);
      if (cancelled) return;

      if (equityResult.status === "fulfilled") {
        setRun((current) => current ? { ...current, equity_curve: equityResult.value } : current);
      } else {
        setEquityError(equityResult.reason instanceof Error ? equityResult.reason.message : BACKTEST_EQUITY_LOAD_FAILED);
      }
      setEquityLoading(false);

      if (signalsResult.status === "fulfilled") {
        setRun((current) => current ? { ...current, signals: signalsResult.value.items } : current);
      } else {
        setSignalsError(signalsResult.reason instanceof Error ? signalsResult.reason.message : BACKTEST_SIGNALS_LOAD_FAILED);
      }
      setSignalsLoading(false);
    };

    const refreshSummary = async () => {
      try {
        const summary = await getBacktestSummary(runId);
        if (cancelled) return;
        setRun({
          ...summary,
          equity_curve: [],
          comparison_curves: {},
          signals: [],
          transactions: [],
        });
        setTransactionCollection((current) => ({
          ...current,
          total: summary.transaction_count,
        }));
        setLoading(false);

        if (summary.status === "queued" || summary.status === "running") {
          const statusItem = await getBacktestWorkerStatus().catch(() => null);
          if (cancelled) return;
          setWorkerStatus(statusItem);
          setWorkerStatusUnavailable(statusItem == null);
          pollTimer = window.setTimeout(refreshSummary, 4000);
          return;
        }

        setWorkerStatusUnavailable(false);
        if (shouldLoadBacktestDetails(summary.status)) {
          await loadCompletedDetails();
        } else {
          setEquityLoading(false);
          setSignalsLoading(false);
          setTransactionsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : BACKTEST_DETAIL_LOAD_FAILED);
          setLoading(false);
        }
      }
    };

    void refreshSummary();

    return () => {
      cancelled = true;
      if (transactionLoadGenerationRef.current === transactionLoadGeneration) {
        transactionLoadGenerationRef.current += 1;
      }
      if (pollTimer != null) window.clearTimeout(pollTimer);
    };
  }, [loadTransactionPages, router.isReady, runId]);

  const retryTransactionCollection = () => {
    if (!runId || transactionCollection.loading) return;
    const restartFromFirstPage = !transactionCollection.nextCursor;
    void loadTransactionPages(
      runId,
      transactionLoadGenerationRef.current,
      transactionCollection.nextCursor,
      restartFromFirstPage,
    );
  };

  const latestPositions = useMemo(
    () => compactBacktestPositions(run?.latest_snapshot?.positions),
    [run]
  );
  const totalReturn = metricNumber(run?.summary_metrics || {}, "total_return");
  const maxDrawdown = metricNumber(run?.summary_metrics || {}, "max_drawdown");
  const signalCount = metricNumber(run?.summary_metrics || {}, "signal_count");
  const tradeCount = metricNumber(run?.summary_metrics || {}, "trade_count");
  const loadedTransactionCount = run?.transactions.length || 0;
  const totalTransactionCount = transactionCollection.total || run?.transaction_count || 0;

  if (!loading && !error && !run) {
    return (
      <AppShell
        title={isZh ? "回测详情" : "Backtest Detail"}
        subtitle={
          isZh
            ? "当前没有找到目标回测记录，可能 run id 不存在，或者后端尚未完成落库"
            : "The target backtest record could not be found. The run ID may not exist or the backend may not have finished persisting it yet."
        }
        actions={actionLink("/backtests", isZh ? "返回回测列表" : "Back To Backtests")}
      >
        <p>{isZh ? "未找到回测记录。" : "Backtest not found."}</p>
      </AppShell>
    );
  }

  return (
    <AppShell
      title={run?.strategy_name ? (isZh ? `${run.strategy_name} 回测结果` : `${run.strategy_name} Backtest Result`) : isZh ? "回测详情" : "Backtest Detail"}
      subtitle={
        isZh
          ? "集中查看单次回测的状态、权益曲线、交易明细和精简持仓，方便快速复盘"
          : "Review run status, the equity curve, transactions, and compact positions in one place."
      }
      actions={
        <>
          {actionLink("/backtests", isZh ? "返回回测列表" : "Back To Backtests")}
          {actionLink(
            run ? `/strategies/${encodeURIComponent(run.strategy_id)}` : "/strategies",
            isZh ? "查看策略" : "View Strategy"
          )}
          {run ? (
            <WorkspaceDialog triggerLabel={isZh ? "运行配置" : "Run Configuration"} title={isZh ? "运行摘要" : "Run Summary"}>
              <ContextStack>
                <ContextGroup title={run.strategy_name || (isZh ? "回测运行" : "Backtest Run")}><ContextStats><ContextStat label={isZh ? "状态" : "Status"} value={run.status} /><ContextStat label={isZh ? "总收益" : "Total return"} value={formatPercent(totalReturn, 2)} /><ContextStat label={isZh ? "最大回撤" : "Max drawdown"} value={formatPercent(maxDrawdown, 2)} /><ContextStat label={isZh ? "信号" : "Signals"} value={signalCount ?? "—"} /><ContextStat label={isZh ? "交易" : "Trades"} value={tradeCount ?? "—"} /><ContextStat label={isZh ? "已载入交易" : "Loaded transactions"} value={`${loadedTransactionCount} / ${totalTransactionCount}`} /></ContextStats></ContextGroup>
                <ContextGroup title={isZh ? "数据状态" : "Data Status"}><ContextStats><ContextStat label={isZh ? "持久化级别" : "Persist level"} value={run.persist_level} /><ContextStat label={isZh ? "最新持仓" : "Latest positions"} value={latestPositions.length} /></ContextStats>{run.persist_level !== "full" ? <ContextNote>{isZh ? "未保存的明细不代表零信号或零交易。" : "Unavailable details do not mean zero signals or trades."}</ContextNote> : null}</ContextGroup>
                <ContextGroup title={isZh ? "快速入口" : "Quick Links"}><ContextLinks><ContextLink href={`/strategies/${encodeURIComponent(run.strategy_id)}`}>{isZh ? "查看策略" : "View strategy"}</ContextLink><ContextLink href="/backtests">{isZh ? "回测列表" : "Backtest list"}</ContextLink></ContextLinks></ContextGroup>
              </ContextStack>
            </WorkspaceDialog>
          ) : null}
        </>
      }
    >
      {router.query.perfProbe === "1" ? <BacktestPerformanceProbe /> : null}
      {loading ? <p>{isZh ? "加载中..." : "Loading..."}</p> : null}
      {error ? (
        <p style={{ color: "#fda4af" }}>
          {error === BACKTEST_DETAIL_LOAD_FAILED
            ? (isZh ? "加载回测详情失败" : "Failed to load backtest detail")
            : error}
        </p>
      ) : null}

      {!loading && !error && run ? (
        <>
          {run.progress ? (
            <div style={{ marginBottom: 18 }}>
              <BacktestProgressBar progress={run.progress} isZh={isZh} showDetails />
            </div>
          ) : null}
          {(run.status === "queued" || run.status === "running")
          && (workerStatusUnavailable || workerStatus?.automation_available === false) ? (
            <div style={{ ...emptyStateStyle, marginBottom: 18, color: "#fde68a" }}>
              {isZh
                ? "任务可排队，但自动执行服务不可用。manager 恢复后会继续处理。"
                : "The job remains queued, but automatic execution is unavailable. Processing will resume when the manager recovers."}
            </div>
          ) : null}
          {run.status === "queued" || run.status === "running" ? (
            <div style={{ ...emptyStateStyle, marginBottom: 18 }}>
              {isZh
                ? "回测正在后台处理。完成前只刷新摘要和 worker 状态，不加载权益、信号或交易明细。"
                : "The backtest is processing in the background. Until completion, only summary and worker status are refreshed; equity, signals, and transactions are not loaded."}
            </div>
          ) : null}
          {run.status === "failed" || run.status === "cancelled" ? (
            <div style={{ ...emptyStateStyle, marginBottom: 18, color: run.status === "failed" ? "#fecaca" : "#fbbf24" }}>
              {run.error_message || (isZh ? `回测已${run.status === "failed" ? "失败" : "取消"}` : `Backtest ${run.status}`)}
            </div>
          ) : null}
          {run.status === "completed" ? (
            <>
          {run.persist_level !== "full" ? (
            <p style={{ ...sectionCardStyle, color: "#fbbf24" }}>
              {isZh
                ? `该回测采用 ${run.persist_level} 持久化级别；未保存的明细不是“零信号”或“零交易”。`
                : `This run uses ${run.persist_level} persistence. Details that were not persisted do not mean zero signals or zero trades.`}
            </p>
          ) : null}
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 16,
              marginBottom: 18,
            }}
          >
            <MetricCard
              label={isZh ? "总收益" : "Total Return"}
              value={formatPercent(totalReturn, 2)}
              hint={isZh ? "来自 strategy_run.summary_metrics.total_return" : "From strategy_run.summary_metrics.total_return"}
              accent="#0f766e"
            />
            <MetricCard
              label={isZh ? "最大回撤" : "Max Drawdown"}
              value={formatPercent(maxDrawdown, 2)}
              hint={isZh ? "用来快速判断这次曲线是否过于激进" : "Used to quickly judge whether the curve was overly aggressive"}
              accent="#b45309"
            />
            <MetricCard
              label="Signals"
              value={signalCount != null ? String(signalCount) : "-"}
              hint={isZh ? "这次 run 中生成的信号数量" : "Number of signals generated in this run"}
              accent="#2563eb"
            />
            <MetricCard
              label="Transactions"
              value={String(run.transaction_count ?? tradeCount ?? "-")}
              hint={isZh ? "已经写入 transactions 的成交记录数量" : "Number of filled transactions written into the transactions table"}
              accent="#ca8a04"
            />
          </section>

          <section
            style={{
              marginBottom: 18,
            }}
          >
            {equityLoading ? (
              <div style={emptyStateStyle}>{isZh ? "正在加载权益曲线..." : "Loading equity curve..."}</div>
            ) : equityError ? (
              <div style={{ ...emptyStateStyle, color: "#fecaca" }}>{detailErrorMessage(equityError, isZh)}</div>
            ) : (
              <EquityCurveCardLightweight
                run={run}
                points={run.equity_curve}
                signals={run.signals}
                transactions={run.transactions}
                initialCash={run.initial_cash}
              />
            )}
            {run.available_details.includes("transactions") ? (
              <div
                style={{
                  marginTop: 8,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 10,
                  flexWrap: "wrap",
                  color: transactionCollection.error ? "#fbbf24" : "rgba(148, 163, 184, 0.9)",
                  fontSize: 12,
                }}
              >
                <span>
                  {transactionsLoading
                    ? isZh
                      ? `正在加载全周期成交标记 ${loadedTransactionCount} / ${totalTransactionCount}`
                      : `Loading full-period fill markers ${loadedTransactionCount} / ${totalTransactionCount}`
                    : transactionCollection.error
                      ? isZh
                        ? `成交标记仅加载 ${loadedTransactionCount} / ${totalTransactionCount}，当前范围不完整。`
                        : `Only ${loadedTransactionCount} / ${totalTransactionCount} fill markers loaded; the current range is incomplete.`
                      : transactionCollection.loading
                        ? isZh
                          ? `正在加载全周期成交标记 ${loadedTransactionCount} / ${totalTransactionCount}`
                          : `Loading full-period fill markers ${loadedTransactionCount} / ${totalTransactionCount}`
                        : transactionCollection.complete
                          ? isZh
                            ? `已加载完整周期成交标记 ${loadedTransactionCount} / ${totalTransactionCount}`
                            : `Full-period fill markers loaded ${loadedTransactionCount} / ${totalTransactionCount}`
                          : null}
                </span>
                {transactionCollection.error ? (
                  <button
                    type="button"
                    onClick={retryTransactionCollection}
                    style={tableToggleButtonStyle}
                  >
                    {isZh ? "继续加载" : "Resume Loading"}
                  </button>
                ) : null}
              </div>
            ) : null}
            {signalsError ? <div style={{ marginTop: 8, color: "#fbbf24", fontSize: 12 }}>{detailErrorMessage(signalsError, isZh)}</div> : null}
          </section>

          {run.available_details.includes("transactions") ? (
            <>
              <section style={{ marginBottom: 18 }}>
                {transactionsLoading || signalsLoading ? (
                  <div style={emptyStateStyle}>{isZh ? "正在加载生命周期明细..." : "Loading lifecycle details..."}</div>
                ) : transactionsError ? (
                  <div style={{ ...emptyStateStyle, color: "#fecaca" }}>{detailErrorMessage(transactionsError, isZh)}</div>
                ) : (
                  <PositionLifecycleCard
                    run={run}
                    transactions={run.transactions}
                    totalTransactions={totalTransactionCount}
                  />
                )}
              </section>
              {transactionsLoading ? (
                <div style={emptyStateStyle}>{isZh ? "正在加载交易明细..." : "Loading transactions..."}</div>
              ) : transactionsError ? (
                <div style={{ ...emptyStateStyle, color: "#fecaca" }}>{detailErrorMessage(transactionsError, isZh)}</div>
              ) : (
                <TransactionsCard
                  runId={run.id}
                  transactions={run.transactions}
                  totalTransactions={totalTransactionCount}
                />
              )}
            </>
          ) : null}

          <section style={{ ...sectionCardStyle, marginTop: 18 }}>
            <div style={{ marginBottom: 16 }}>
              <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>{isZh ? "最新持仓" : "Latest Positions"}</h2>
              <p style={sectionSubtitleStyle}>
                {isZh ? "仅展示复盘最常用的数量、成本、收盘价和市值。" : "Shows only quantity, cost, closing price, and market value for a quick review."}
              </p>
            </div>

            {latestPositions.length === 0 ? (
              <div style={emptyStateStyle}>{isZh ? "当前没有持仓，或回测结束时已经全部平仓" : "There are no positions, or all positions were closed by the end of the backtest"}</div>
            ) : (
              <div style={positionGridStyle}>
                {latestPositions.map((position) => (
                  <article key={position.symbol} style={positionCardStyle}>
                    <strong style={{ color: "#f8fafc", fontSize: 18 }}>{position.symbol}</strong>
                    <div style={positionMetricsStyle}>
                      <div><span style={positionLabelStyle}>{isZh ? "数量" : "Quantity"}</span><strong>{position.quantity?.toLocaleString(locale, { maximumFractionDigits: 4 }) ?? "-"}</strong></div>
                      <div><span style={positionLabelStyle}>{isZh ? "成本价" : "Avg. cost"}</span><strong>{formatCurrency(position.averageEntryPrice, locale)}</strong></div>
                      <div><span style={positionLabelStyle}>{isZh ? "收盘价" : "Close"}</span><strong>{formatCurrency(position.closePrice, locale)}</strong></div>
                      <div><span style={positionLabelStyle}>{isZh ? "市值" : "Market value"}</span><strong>{formatCurrency(position.marketValue, locale)}</strong></div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
            </>
          ) : null}
        </>
      ) : null}
    </AppShell>
  );
}

const sectionCardStyle = {
  padding: 22,
  borderRadius: 24,
  border: "1px solid rgba(71, 85, 105, 0.28)",
  background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
  color: "#e2e8f0",
  boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
} as const;

const sectionSubtitleStyle = {
  margin: 0,
  color: "rgba(148, 163, 184, 0.88)",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const positionGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(260px, 100%), 1fr))",
  gap: 12,
} as const;

const positionCardStyle = {
  display: "grid",
  gap: 12,
  padding: 16,
  borderRadius: 18,
  border: "1px solid rgba(71, 85, 105, 0.28)",
  background: "rgba(15, 23, 42, 0.76)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const positionMetricsStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 12,
  color: "#e2e8f0",
} as const;

const positionLabelStyle = {
  display: "block",
  marginBottom: 4,
  color: "#64748b",
  fontSize: 12,
  fontWeight: 700,
} as const;

const emptyStateStyle = {
  padding: 16,
  borderRadius: 16,
  background: "rgba(15, 23, 42, 0.7)",
  border: "1px solid rgba(71, 85, 105, 0.28)",
  color: "rgba(148, 163, 184, 0.88)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const infoGridStyle = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
  gap: 14,
} as const;

const labelStyle = {
  marginBottom: 6,
  color: "#64748b",
  fontSize: 12,
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const valueStyle = {
  color: "#e2e8f0",
  lineHeight: 1.6,
  wordBreak: "break-word" as const,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const miniMetricStyle = {
  padding: 14,
  borderRadius: 16,
  background: "rgba(15, 23, 42, 0.72)",
  border: "1px solid rgba(71, 85, 105, 0.28)",
  color: "#e2e8f0",
} as const;

const miniMetricValueStyle = {
  color: "#f8fafc",
  fontWeight: 700,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
} as const;

const legendItemStyle = {
  display: "inline-flex",
  alignItems: "center",
  gap: 8,
} as const;

const legendDotStyle = {
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "999px",
} as const;

function legendLineStyle(color: string) {
  return {
    display: "inline-block",
    width: 18,
    height: 0,
    borderTop: `3px solid ${color}`,
    borderRadius: 999,
  } as const;
}

const legendTriangleUpStyle = {
  width: 0,
  height: 0,
  borderLeft: "6px solid transparent",
  borderRight: "6px solid transparent",
  borderBottom: "10px solid #16a34a",
} as const;

const legendTriangleDownStyle = {
  width: 0,
  height: 0,
  borderLeft: "6px solid transparent",
  borderRight: "6px solid transparent",
  borderTop: "10px solid #dc2626",
} as const;

const cellStyle = {
  padding: "12px 14px",
  color: "#e2e8f0",
  fontSize: 14,
  lineHeight: 1.5,
} as const;

const lifecycleStatusCellStyle = {
  ...cellStyle,
  width: 96,
  whiteSpace: "nowrap" as const,
};

const lifecycleStatusBadgeStyle = {
  minWidth: 64,
  justifyContent: "center",
  whiteSpace: "nowrap" as const,
};

const lifecyclePrimaryCellStyle = {
  display: "inline-flex",
  alignItems: "center",
  gap: 10,
  fontWeight: 700,
};

function lifecycleChevronStyle(expanded: boolean) {
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: 16,
    color: expanded ? "#67e8f9" : "rgba(148, 163, 184, 0.86)",
    fontSize: 12,
    transform: expanded ? "translateY(0)" : "translateY(-0.5px)",
  } as const;
}

const lifecycleExpandedCellStyle = {
  padding: "0 14px 16px",
  background: "rgba(8, 15, 24, 0.68)",
};

function lifecycleSortButtonStyle(active: boolean) {
  return {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "flex-start",
    gap: 6,
    width: "100%",
    padding: 0,
    border: "none",
    background: "transparent",
    color: active ? "#e2e8f0" : "rgba(148, 163, 184, 0.88)",
    cursor: "pointer",
    font: "inherit",
    letterSpacing: "inherit",
    textTransform: "inherit" as const,
    textAlign: "left" as const,
  };
}

function lifecycleSortIndicatorStyle(active: boolean) {
  return {
    color: active ? "#67e8f9" : "rgba(100, 116, 139, 0.85)",
    fontSize: 11,
    lineHeight: 1,
  } as const;
}

const lifecycleDetailPanelStyle = {
  padding: 18,
  borderRadius: 18,
  border: "1px solid rgba(56, 189, 248, 0.16)",
  background: "linear-gradient(180deg, rgba(8,15,24,0.96), rgba(15,23,42,0.9))",
  boxShadow: "inset 0 1px 0 rgba(148, 163, 184, 0.08)",
};

const stickyTableHeaderCellStyle = {
  position: "sticky" as const,
  top: 0,
  zIndex: 2,
  background: "rgba(30, 41, 59, 0.96)",
  backdropFilter: "blur(10px)",
  borderBottom: "1px solid rgba(71, 85, 105, 0.32)",
  boxShadow: "0 10px 24px rgba(2, 6, 23, 0.18)",
} as const;
