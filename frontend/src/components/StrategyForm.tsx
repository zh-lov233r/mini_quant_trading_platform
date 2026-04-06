import React, { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";

import {
  createStrategy,
  getStrategyCatalog,
  getStrategyFeatureSupport,
  updateStrategyConfig,
} from "@/api/strategies";
import { useI18n } from "@/i18n/provider";
import type {
  StrategyCatalogItem,
  StrategyCreate,
  StrategyFeatureSupport,
  StrategyOut,
  StrategyStatus,
  StrategyType,
} from "@/types/strategy";

function toRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as Record<string, unknown>;
}

function toFiniteNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function toStringValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function toSymbolText(value: unknown): string {
  if (!Array.isArray(value)) {
    return "";
  }
  return value
    .map((item) => String(item).trim().toUpperCase())
    .filter(Boolean)
    .join(",");
}

function isStrategyType(value: unknown): value is StrategyType {
  return value === "trend" || value === "mean_reversion" || value === "island_reversal" || value === "custom";
}

function isStrategyStatus(value: unknown): value is StrategyStatus {
  return value === "draft" || value === "active" || value === "archived";
}

interface StrategyFormProps {
  mode?: "create" | "edit";
  initialStrategy?: StrategyOut | null;
}

export default function StrategyForm({
  mode = "create",
  initialStrategy = null,
}: StrategyFormProps) {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const isEditMode = mode === "edit";
  const initialParams = toRecord(initialStrategy?.params);
  const initialSignal = toRecord(initialParams.signal);
  const initialUniverse = toRecord(initialParams.universe);
  const initialRisk = toRecord(initialParams.risk);
  const initialExecution = toRecord(initialParams.execution);
  const initialFastIndicator = toRecord(initialSignal.fast_indicator);
  const initialSlowIndicator = toRecord(initialSignal.slow_indicator);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [featureSupport, setFeatureSupport] = useState<StrategyFeatureSupport | null>(null);
  const [featureSupportError, setFeatureSupportError] = useState<string | null>(null);
  const [name, setName] = useState(
    initialStrategy?.name ?? "Trend_EMA15_SMA200"
  );
  const [description, setDescription] = useState(
    initialStrategy
      ? initialStrategy.description
        ?? toStringValue(toRecord(initialParams.metadata).description, "")
      : isZh
        ? "双均线趋势策略"
        : "Dual moving average trend strategy"
  );
  const [strategyType, setStrategyType] = useState<StrategyType>(
    isStrategyType(initialStrategy?.strategy_type) ? initialStrategy.strategy_type : "trend"
  );
  const [status, setStatus] = useState<StrategyStatus>(
    isStrategyStatus(initialStrategy?.status) ? initialStrategy.status : "draft"
  );
  const [fastKind, setFastKind] = useState<"ema" | "sma">(
    initialFastIndicator.kind === "sma" ? "sma" : "ema"
  );
  const [fastWindow, setFastWindow] = useState(
    toFiniteNumber(initialFastIndicator.window, 15)
  );
  const [slowKind, setSlowKind] = useState<"ema" | "sma">(
    initialSlowIndicator.kind === "ema" ? "ema" : "sma"
  );
  const [slowWindow, setSlowWindow] = useState(
    toFiniteNumber(initialSlowIndicator.window, 200)
  );
  const [volMul, setVolMul] = useState(
    toFiniteNumber(initialSignal.volume_multiplier, 1.5)
  );
  const [atrMul, setAtrMul] = useState(
    toFiniteNumber(initialSignal.atr_multiplier, 2.0)
  );
  const [symbols, setSymbols] = useState(toSymbolText(initialUniverse.symbols));
  const [maxPositions, setMaxPositions] = useState(
    toFiniteNumber(initialRisk.max_positions, 10)
  );
  const [positionSizePct, setPositionSizePct] = useState(
    toFiniteNumber(initialRisk.position_size_pct, 0.1)
  );
  const [rebalance, setRebalance] = useState(
    toStringValue(initialExecution.rebalance, "daily")
  );
  const [runAt, setRunAt] = useState(
    toStringValue(initialExecution.run_at, "close")
  );
  const [islandDowntrendLookback, setIslandDowntrendLookback] = useState(
    toFiniteNumber(initialSignal.downtrend_lookback, 60)
  );
  const [islandDowntrendMinDropPct, setIslandDowntrendMinDropPct] = useState(
    toFiniteNumber(initialSignal.downtrend_min_drop_pct, 0.15)
  );
  const [leftGapMinPct, setLeftGapMinPct] = useState(
    toFiniteNumber(initialSignal.left_gap_min_pct, 0.02)
  );
  const [rightGapMinPct, setRightGapMinPct] = useState(
    toFiniteNumber(initialSignal.right_gap_min_pct, 0.02)
  );
  const [minIslandBars, setMinIslandBars] = useState(
    toFiniteNumber(initialSignal.min_island_bars, 1)
  );
  const [maxIslandBars, setMaxIslandBars] = useState(
    toFiniteNumber(initialSignal.max_island_bars, 8)
  );
  const [leftVolumeRatioMax, setLeftVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.left_volume_ratio_max, 0.8)
  );
  const [rightVolumeRatioMin, setRightVolumeRatioMin] = useState(
    toFiniteNumber(initialSignal.right_volume_ratio_min, 1.5)
  );
  const [retestWindow, setRetestWindow] = useState(
    toFiniteNumber(initialSignal.retest_window, 10)
  );
  const [retestVolumeRatioMax, setRetestVolumeRatioMax] = useState(
    toFiniteNumber(initialSignal.retest_volume_ratio_max, 0.7)
  );
  const [supportTolerancePct, setSupportTolerancePct] = useState(
    toFiniteNumber(initialSignal.support_tolerance_pct, 0.01)
  );
  const [islandStopLossAtr, setIslandStopLossAtr] = useState(
    toFiniteNumber(initialRisk.stop_loss_atr, 1.5)
  );
  const [rawJson, setRawJson] = useState(
    initialStrategy
      ? JSON.stringify(initialStrategy.params, null, 2)
      : "{\n  \"rules\": []\n}"
  );
  const [resp, setResp] = useState<StrategyOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const selectedTemplate = useMemo(
    () => catalog.find((item) => item.strategy_type === strategyType) || null,
    [catalog, strategyType]
  );

  useEffect(() => {
    let cancelled = false;
    Promise.all([getStrategyCatalog(), getStrategyFeatureSupport()])
      .then(([items, support]) => {
        if (!cancelled) {
          setCatalog(items);
          setFeatureSupport(support);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setCatalogError(error.message || (isZh ? "无法加载策略模板" : "Unable to load strategy templates"));
          setFeatureSupportError(
            error.message || (isZh ? "无法加载数据库支持的指标配置" : "Unable to load supported indicator settings from the database")
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isZh]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (
      strategyType === "trend"
      || strategyType === "island_reversal"
      || catalog.length === 0
    ) {
      return;
    }
    const item = catalog.find((entry) => entry.strategy_type === strategyType);
    if (item) {
      setRawJson(JSON.stringify(item.defaults, null, 2));
    }
  }, [catalog, isEditMode, strategyType]);

  useEffect(() => {
    if (isEditMode) {
      return;
    }
    if (strategyType !== "island_reversal" || !selectedTemplate) {
      return;
    }

    const defaults = toRecord(selectedTemplate.defaults);
    const signal = toRecord(defaults.signal);
    const universe = toRecord(defaults.universe);
    const risk = toRecord(defaults.risk);
    const execution = toRecord(defaults.execution);

    setIslandDowntrendLookback(toFiniteNumber(signal.downtrend_lookback, 60));
    setIslandDowntrendMinDropPct(toFiniteNumber(signal.downtrend_min_drop_pct, 0.15));
    setLeftGapMinPct(toFiniteNumber(signal.left_gap_min_pct, 0.02));
    setRightGapMinPct(toFiniteNumber(signal.right_gap_min_pct, 0.02));
    setMinIslandBars(toFiniteNumber(signal.min_island_bars, 1));
    setMaxIslandBars(toFiniteNumber(signal.max_island_bars, 8));
    setLeftVolumeRatioMax(toFiniteNumber(signal.left_volume_ratio_max, 0.8));
    setRightVolumeRatioMin(toFiniteNumber(signal.right_volume_ratio_min, 1.5));
    setRetestWindow(toFiniteNumber(signal.retest_window, 10));
    setRetestVolumeRatioMax(toFiniteNumber(signal.retest_volume_ratio_max, 0.7));
    setSupportTolerancePct(toFiniteNumber(signal.support_tolerance_pct, 0.01));
    setSymbols(toSymbolText(universe.symbols));
    setMaxPositions(toFiniteNumber(risk.max_positions, 6));
    setPositionSizePct(toFiniteNumber(risk.position_size_pct, 0.15));
    setIslandStopLossAtr(toFiniteNumber(risk.stop_loss_atr, 1.5));
    setRebalance(toStringValue(execution.rebalance, "daily"));
    setRunAt(toStringValue(execution.run_at, "close"));
  }, [isEditMode, selectedTemplate, strategyType]);

  const fastWindowOptions = useMemo(
    () =>
      fastKind === "ema"
        ? featureSupport?.trend.ema_windows || []
        : featureSupport?.trend.sma_windows || [],
    [fastKind, featureSupport]
  );

  const slowWindowOptions = useMemo(
    () =>
      slowKind === "ema"
        ? featureSupport?.trend.ema_windows || []
        : featureSupport?.trend.sma_windows || [],
    [slowKind, featureSupport]
  );

  useEffect(() => {
    if (fastWindowOptions.length === 0) {
      return;
    }
    if (!fastWindowOptions.includes(Number(fastWindow))) {
      setFastWindow(fastWindowOptions[0]);
    }
  }, [fastWindow, fastWindowOptions]);

  useEffect(() => {
    if (slowWindowOptions.length === 0) {
      return;
    }
    if (!slowWindowOptions.includes(Number(slowWindow))) {
      setSlowWindow(slowWindowOptions[0]);
    }
  }, [slowWindow, slowWindowOptions]);

  const trendParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
      signal: {
        fast_indicator: {
          kind: fastKind,
          window: Number(fastWindow),
        },
        slow_indicator: {
          kind: slowKind,
          window: Number(slowWindow),
        },
        volume_multiplier: Number(volMul),
        atr_multiplier: Number(atrMul),
        price_field: "close",
        trigger: "cross_over",
      },
      universe: {
        symbols: parsedSymbols,
        selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
      },
      risk: {
        max_positions: Number(maxPositions),
        position_size_pct: Number(positionSizePct),
        stop_loss_atr: Number(atrMul),
      },
      execution: {
        timeframe: "1d",
        rebalance,
        run_at: runAt,
      },
      metadata: {
        description,
        schema_version: 1,
      },
      };
    },
    [
      atrMul,
      description,
      fastKind,
      fastWindow,
      maxPositions,
      positionSizePct,
      rebalance,
      runAt,
      slowKind,
      slowWindow,
      symbols,
      volMul,
    ]
  );

  const islandReversalParams = useMemo(
    () => {
      const parsedSymbols = symbols
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean);

      return {
        signal: {
          downtrend_lookback: Number(islandDowntrendLookback),
          downtrend_min_drop_pct: Number(islandDowntrendMinDropPct),
          left_gap_min_pct: Number(leftGapMinPct),
          right_gap_min_pct: Number(rightGapMinPct),
          min_island_bars: Number(minIslandBars),
          max_island_bars: Number(maxIslandBars),
          left_volume_ratio_max: Number(leftVolumeRatioMax),
          right_volume_ratio_min: Number(rightVolumeRatioMin),
          retest_window: Number(retestWindow),
          retest_volume_ratio_max: Number(retestVolumeRatioMax),
          support_tolerance_pct: Number(supportTolerancePct),
        },
        universe: {
          symbols: parsedSymbols,
          selection_mode: parsedSymbols.length > 0 ? "manual" : "all_common_stock",
        },
        risk: {
          max_positions: Number(maxPositions),
          position_size_pct: Number(positionSizePct),
          stop_loss_atr: Number(islandStopLossAtr),
        },
        execution: {
          timeframe: "1d",
          rebalance,
          run_at: runAt,
        },
        metadata: {
          description,
          schema_version: 1,
        },
      };
    },
    [
      description,
      islandDowntrendLookback,
      islandDowntrendMinDropPct,
      islandStopLossAtr,
      leftGapMinPct,
      leftVolumeRatioMax,
      maxIslandBars,
      maxPositions,
      minIslandBars,
      positionSizePct,
      rebalance,
      retestVolumeRatioMax,
      retestWindow,
      rightGapMinPct,
      rightVolumeRatioMin,
      runAt,
      supportTolerancePct,
      symbols,
    ]
  );

  const previewPayload = useMemo<StrategyCreate>(() => {
    if (strategyType === "trend") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: trendParams,
      };
    }
    if (strategyType === "island_reversal") {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: islandReversalParams,
      };
    }

    try {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: JSON.parse(rawJson),
      };
    } catch {
      return {
        name,
        description,
        strategy_type: strategyType,
        status,
        params: {},
      };
    }
  }, [description, islandReversalParams, name, rawJson, status, strategyType, trendParams]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setResp(null);
    setLoading(true);

    try {
      if (!name.trim()) {
        throw new Error(isZh ? "策略名不能为空" : "Strategy name cannot be empty");
      }

      let payload: StrategyCreate;
      if (strategyType === "trend") {
        if (!(Number(fastWindow) > 0)) throw new Error(isZh ? "短周期必须 > 0" : "Fast window must be > 0");
        if (!(Number(slowWindow) > 0)) throw new Error(isZh ? "长周期必须 > 0" : "Slow window must be > 0");
        if (fastWindowOptions.length > 0 && !fastWindowOptions.includes(Number(fastWindow))) {
          throw new Error(
            isZh
              ? `当前数据库不支持快线 ${fastKind.toUpperCase()}${fastWindow}，可用周期: ${fastWindowOptions.join(", ")}`
              : `The database does not support fast line ${fastKind.toUpperCase()}${fastWindow}. Supported windows: ${fastWindowOptions.join(", ")}`
          );
        }
        if (slowWindowOptions.length > 0 && !slowWindowOptions.includes(Number(slowWindow))) {
          throw new Error(
            isZh
              ? `当前数据库不支持慢线 ${slowKind.toUpperCase()}${slowWindow}，可用周期: ${slowWindowOptions.join(", ")}`
              : `The database does not support slow line ${slowKind.toUpperCase()}${slowWindow}. Supported windows: ${slowWindowOptions.join(", ")}`
          );
        }
        if (!(Number(volMul) > 0)) throw new Error(isZh ? "成交量过滤倍数必须 > 0" : "Volume multiplier must be > 0");
        if (!(Number(atrMul) > 0)) throw new Error(isZh ? "ATR 乘数必须 > 0" : "ATR multiplier must be > 0");
        if (!(Number(maxPositions) > 0)) throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: trendParams,
        };
      } else if (strategyType === "island_reversal") {
        if (!(Number(islandDowntrendLookback) > 0)) {
          throw new Error(isZh ? "下跌回看窗口必须 > 0" : "Downtrend lookback must be > 0");
        }
        if (!(Number(islandDowntrendMinDropPct) > 0 && Number(islandDowntrendMinDropPct) <= 1)) {
          throw new Error(isZh ? "最低下跌幅度必须在 (0, 1] 之间" : "Min downtrend drop must be within (0, 1]");
        }
        if (!(Number(leftGapMinPct) > 0 && Number(leftGapMinPct) <= 1)) {
          throw new Error(isZh ? "左侧缺口最小幅度必须在 (0, 1] 之间" : "Left gap min pct must be within (0, 1]");
        }
        if (!(Number(rightGapMinPct) > 0 && Number(rightGapMinPct) <= 1)) {
          throw new Error(isZh ? "右侧缺口最小幅度必须在 (0, 1] 之间" : "Right gap min pct must be within (0, 1]");
        }
        if (!(Number(minIslandBars) > 0)) {
          throw new Error(isZh ? "最少岛区 K 线数必须 > 0" : "Min island bars must be > 0");
        }
        if (!(Number(maxIslandBars) >= Number(minIslandBars))) {
          throw new Error(isZh ? "最多岛区 K 线数不能小于最少岛区 K 线数" : "Max island bars cannot be less than min island bars");
        }
        if (!(Number(leftVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "左侧缩量上限必须 > 0" : "Left volume ratio max must be > 0");
        }
        if (!(Number(rightVolumeRatioMin) > 0)) {
          throw new Error(isZh ? "右侧放量下限必须 > 0" : "Right volume ratio min must be > 0");
        }
        if (!(Number(retestWindow) > 0)) {
          throw new Error(isZh ? "回踩观察窗口必须 > 0" : "Retest window must be > 0");
        }
        if (!(Number(retestVolumeRatioMax) > 0)) {
          throw new Error(isZh ? "回踩缩量上限必须 > 0" : "Retest volume ratio max must be > 0");
        }
        if (!(Number(supportTolerancePct) > 0 && Number(supportTolerancePct) <= 1)) {
          throw new Error(isZh ? "缺口支撑容差必须在 (0, 1] 之间" : "Support tolerance pct must be within (0, 1]");
        }
        if (!(Number(maxPositions) > 0)) {
          throw new Error(isZh ? "最大持仓数必须 > 0" : "Max positions must be > 0");
        }
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error(isZh ? "单票仓位比例必须在 (0, 1] 之间" : "Position size percentage must be within (0, 1]");
        }
        if (!(Number(islandStopLossAtr) > 0)) {
          throw new Error(isZh ? "ATR 止损倍数必须 > 0" : "ATR stop loss must be > 0");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: islandReversalParams,
        };
      } else {
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: JSON.parse(rawJson),
        };
      }

      const data = isEditMode && initialStrategy
        ? await updateStrategyConfig(initialStrategy.id, {
            description: payload.description?.trim() ?? "",
            status,
            params: payload.params,
          })
        : await createStrategy(
            payload,
            (crypto as any)?.randomUUID?.() || String(Date.now())
          );
      setResp(data);
      if (isEditMode) {
        await router.push(`/strategies/${encodeURIComponent(data.id)}`);
      } else if (typeof window !== "undefined" && window.history.length > 1) {
        router.back();
      } else {
        await router.push("/strategies");
      }
    } catch (error: any) {
      setErr(error?.message || (isZh ? "提交失败" : "Submit failed"));
    } finally {
      setLoading(false);
    }
  };

  const boxStyle: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    margin: "0 0 12px",
  };
  const inputStyle: React.CSSProperties = {
    padding: 12,
    border: "1px solid rgba(71, 85, 105, 0.34)",
    borderRadius: 14,
    fontSize: 14,
    background: "rgba(8, 15, 24, 0.82)",
    color: "#e2e8f0",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
  const cardStyle: React.CSSProperties = {
    padding: 22,
    border: "1px solid rgba(71, 85, 105, 0.3)",
    borderRadius: 24,
    background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
    color: "#e2e8f0",
    boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
  };

  return (
    <form
      id="strategy-create-form"
      onSubmit={submit}
      style={{
        margin: 0,
        padding: "0 0 48px",
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        color: "#e2e8f0",
      }}
    >
      <div
        style={{
          marginBottom: 16,
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button
          type="button"
          onClick={() => {
            if (typeof window !== "undefined" && window.history.length > 1) {
              router.back();
            } else {
              router.push("/strategies");
            }
          }}
          style={{
            padding: "10px 14px",
            borderRadius: 14,
            border: "1px solid rgba(71, 85, 105, 0.34)",
            background: "rgba(15, 23, 42, 0.76)",
            color: "#e2e8f0",
            fontWeight: 700,
            cursor: "pointer",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          }}
        >
          {isZh ? "返回上一页" : "Back"}
        </button>

        <button
          type="submit"
          disabled={loading}
          style={{
            padding: "10px 14px",
            borderRadius: 14,
            border: "none",
            background: "#0891b2",
            color: "#f8fafc",
            fontWeight: 700,
            cursor: loading ? "progress" : "pointer",
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            opacity: loading ? 0.72 : 1,
          }}
        >
          {loading
            ? (isZh ? "提交中…" : "Submitting...")
            : isEditMode
              ? (isZh ? "保存参数修改" : "Save Parameter Changes")
              : isZh
                ? "保存策略"
                : "Save Strategy"}
        </button>
      </div>

      {catalogError && (
        <div style={{ color: "#fda4af", marginBottom: 16 }}>{catalogError}</div>
      )}
      {featureSupportError && (
        <div style={{ color: "#fdba74", marginBottom: 16 }}>
          {featureSupportError}
        </div>
      )}
      {isEditMode ? (
        <div
          style={{
            marginBottom: 16,
            padding: "12px 14px",
            borderRadius: 16,
            border: "1px solid rgba(56, 189, 248, 0.22)",
            background: "rgba(8, 47, 73, 0.32)",
            color: "#bae6fd",
            lineHeight: 1.6,
          }}
        >
          {isZh
            ? "当前页面用于编辑策略参数。策略名称仍可在详情页的改名区域单独维护，策略类型在这里保持锁定。"
            : "This page edits persisted strategy parameters. Rename stays on the detail page, and the strategy type remains locked here."}
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1.3fr) minmax(320px, 1fr)",
          gap: 20,
          alignItems: "start",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section style={cardStyle}>
            <div style={boxStyle}>
              <label>{isZh ? "策略名" : "Strategy Name"}</label>
              <input
                style={inputStyle}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={isEditMode}
                required
              />
            </div>

            <div style={boxStyle}>
              <label>{isZh ? "策略说明" : "Description"}</label>
              <textarea
                style={{ ...inputStyle, minHeight: 90, resize: "vertical" }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={
                  isZh
                    ? "例如：收盘后扫描趋势股，第二天开盘前生成调仓建议"
                    : "For example: scan trend candidates after close and generate rebalance suggestions before next open"
                }
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div style={boxStyle}>
                <label>{isZh ? "策略类型" : "Strategy Type"}</label>
                <select
                  style={inputStyle}
                  value={strategyType}
                  onChange={(e) => setStrategyType(e.target.value as StrategyType)}
                  disabled={isEditMode}
                >
                  {catalog.length === 0 && <option value="trend">trend</option>}
                  {catalog.map((item) => (
                    <option key={item.strategy_type} value={item.strategy_type}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "状态" : "Status"}</label>
                <select
                  style={inputStyle}
                  value={status}
                  onChange={(e) => setStatus(e.target.value as StrategyStatus)}
                >
                  <option value="draft">draft</option>
                  <option value="active">active</option>
                  <option value="archived">archived</option>
                </select>
              </div>
            </div>
          </section>

          {strategyType === "trend" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "趋势参数" : "Trend Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh ? "当前数据库支持的趋势均线周期:" : "Trend moving-average windows supported by the database:"}
                {" "}
                EMA {featureSupport?.trend.ema_windows.join(", ") || (isZh ? "加载中" : "Loading")}
                {" "}
                | SMA {featureSupport?.trend.sma_windows.join(", ") || (isZh ? "加载中" : "Loading")}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "快线类型" : "Fast Line Type"}</label>
                  <select
                    style={inputStyle}
                    value={fastKind}
                    onChange={(e) => setFastKind(e.target.value as "ema" | "sma")}
                  >
                    <option value="ema">EMA</option>
                    <option value="sma">SMA</option>
                  </select>
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "快线周期" : "Fast Window"}</label>
                  <select
                    style={inputStyle}
                    value={fastWindow}
                    onChange={(e) => setFastWindow(Number(e.target.value))}
                  >
                    {fastWindowOptions.length === 0 ? (
                      <option value={fastWindow}>{fastWindow}</option>
                    ) : (
                      fastWindowOptions.map((window) => (
                        <option key={`fast-${fastKind}-${window}`} value={window}>
                          {window}
                        </option>
                      ))
                    )}
                  </select>
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "慢线类型" : "Slow Line Type"}</label>
                  <select
                    style={inputStyle}
                    value={slowKind}
                    onChange={(e) => setSlowKind(e.target.value as "ema" | "sma")}
                  >
                    <option value="ema">EMA</option>
                    <option value="sma">SMA</option>
                  </select>
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "慢线周期" : "Slow Window"}</label>
                  <select
                    style={inputStyle}
                    value={slowWindow}
                    onChange={(e) => setSlowWindow(Number(e.target.value))}
                  >
                    {slowWindowOptions.length === 0 ? (
                      <option value={slowWindow}>{slowWindow}</option>
                    ) : (
                      slowWindowOptions.map((window) => (
                        <option key={`slow-${slowKind}-${window}`} value={window}>
                          {window}
                        </option>
                      ))
                    )}
                  </select>
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "成交量过滤倍数" : "Volume Multiplier"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={volMul}
                    onChange={(e) => setVolMul(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 乘数" : "ATR Multiplier"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={atrMul}
                    onChange={(e) => setAtrMul(Number(e.target.value))}
                  />
                </div>
              </div>

                <div style={boxStyle}>
                  <label>{isZh ? "股票池" : "Universe"}</label>
                  <input
                    style={inputStyle}
                    value={symbols}
                    onChange={(e) => setSymbols(e.target.value)}
                    placeholder={
                      isZh
                        ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                        : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                    }
                  />
                </div>
                <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                  {isZh
                    ? "当前默认行为：如果股票池留空，策略会把 universe 解释为全部 active US common stock。"
                    : "Current default behavior: if universe is left empty, the strategy interprets it as all active US common stocks."}
                </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="0.01"
                    style={inputStyle}
                    value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                  <select
                    style={inputStyle}
                    value={rebalance}
                    onChange={(e) => setRebalance(e.target.value)}
                  >
                    <option value="daily">daily</option>
                    <option value="weekly">weekly</option>
                    <option value="monthly">monthly</option>
                  </select>
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "运行时机" : "Run Timing"}</label>
                <select
                  style={inputStyle}
                  value={runAt}
                  onChange={(e) => setRunAt(e.target.value)}
                >
                  <option value="close">close</option>
                  <option value="open">open</option>
                </select>
              </div>
            </section>
          ) : strategyType === "island_reversal" ? (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "岛形反转参数" : "Island Reversal Parameters"}</h3>
              <div style={{ marginBottom: 14, color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "涉及幅度的字段都使用小数表示百分比，例如 0.02 = 2%，0.15 = 15%。"
                  : "Percent-style thresholds use decimals, for example 0.02 = 2% and 0.15 = 15%."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "下跌回看窗口" : "Downtrend Lookback"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={islandDowntrendLookback}
                    onChange={(e) => setIslandDowntrendLookback(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最低下跌幅度" : "Min Downtrend Drop"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={islandDowntrendMinDropPct}
                    onChange={(e) => setIslandDowntrendMinDropPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "左侧缺口最小幅度" : "Left Gap Min Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={leftGapMinPct}
                    onChange={(e) => setLeftGapMinPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "右侧缺口最小幅度" : "Right Gap Min Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={rightGapMinPct}
                    onChange={(e) => setRightGapMinPct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最少岛区 K 线数" : "Min Island Bars"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={minIslandBars}
                    onChange={(e) => setMinIslandBars(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "最多岛区 K 线数" : "Max Island Bars"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={maxIslandBars}
                    onChange={(e) => setMaxIslandBars(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "左侧缩量上限" : "Left Volume Ratio Max"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={leftVolumeRatioMax}
                    onChange={(e) => setLeftVolumeRatioMax(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "右侧放量下限" : "Right Volume Ratio Min"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={rightVolumeRatioMin}
                    onChange={(e) => setRightVolumeRatioMin(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "回踩观察窗口" : "Retest Window"}</label>
                  <input
                    type="number"
                    min={1}
                    step="1"
                    style={inputStyle}
                    value={retestWindow}
                    onChange={(e) => setRetestWindow(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "回踩缩量上限" : "Retest Volume Ratio Max"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={retestVolumeRatioMax}
                    onChange={(e) => setRetestVolumeRatioMax(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "缺口支撑容差" : "Support Tolerance Pct"}</label>
                  <input
                    type="number"
                    min={0.001}
                    max={1}
                    step="any"
                    style={inputStyle}
                    value={supportTolerancePct}
                    onChange={(e) => setSupportTolerancePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "ATR 止损倍数" : "ATR Stop Loss"}</label>
                  <input
                    type="number"
                    min={0.1}
                    step="0.1"
                    style={inputStyle}
                    value={islandStopLossAtr}
                    onChange={(e) => setIslandStopLossAtr(Number(e.target.value))}
                  />
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "股票池" : "Universe"}</label>
                <input
                  style={inputStyle}
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  placeholder={
                    isZh
                      ? "留空则默认绑定全部 common stock；也可以手动输入 AAPL,MSFT,NVDA"
                      : "Leave empty to use all common stocks by default, or enter symbols like AAPL,MSFT,NVDA"
                  }
                />
              </div>
              <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, lineHeight: 1.6 }}>
                {isZh
                  ? "股票池留空时，会在全部 common stock 中扫描岛形反转形态。"
                  : "If the universe is empty, the strategy scans for island reversal setups across all common stocks."}
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>{isZh ? "最大持仓数" : "Max Positions"}</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "单票仓位比例" : "Position Size Pct"}</label>
                  <input
                    type="number"
                    min={0.01}
                    max={1}
                    step="0.01"
                    style={inputStyle}
                    value={positionSizePct}
                    onChange={(e) => setPositionSizePct(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>{isZh ? "调仓频率" : "Rebalance Frequency"}</label>
                  <select
                    style={inputStyle}
                    value={rebalance}
                    onChange={(e) => setRebalance(e.target.value)}
                  >
                    <option value="daily">daily</option>
                    <option value="weekly">weekly</option>
                    <option value="monthly">monthly</option>
                  </select>
                </div>
              </div>

              <div style={boxStyle}>
                <label>{isZh ? "运行时机" : "Run Timing"}</label>
                <select
                  style={inputStyle}
                  value={runAt}
                  onChange={(e) => setRunAt(e.target.value)}
                >
                  <option value="close">close</option>
                  <option value="open">open</option>
                </select>
              </div>
            </section>
          ) : (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "高级 JSON 配置" : "Advanced JSON Config"}</h3>
              <p style={{ marginTop: 0, color: "rgba(148, 163, 184, 0.88)", lineHeight: 1.6 }}>
                {selectedTemplate?.engine_ready
                  ? (
                    isZh
                      ? "这个策略类型已经接入后端执行器。这里直接编辑 JSON 参数模板，保存后即可用于回测和 paper trading。"
                      : "This strategy type is already wired into the backend evaluator. Edit the JSON template here and save it for backtesting and paper trading."
                  )
                  : (
                    isZh
                      ? "该策略类型目前先以 JSON/DSL 形式落库，当前后端支持存储和查询，等专门 evaluator 接好后即可执行。"
                      : "This strategy type is currently stored as JSON/DSL. The backend already supports persistence and retrieval, and it can execute once a dedicated evaluator is wired in."
                  )}
              </p>
              <textarea
                style={{
                  ...inputStyle,
                  minHeight: 320,
                  resize: "vertical",
                  fontFamily: "SFMono-Regular, Consolas, monospace",
                }}
                value={rawJson}
                onChange={(e) => setRawJson(e.target.value)}
              />
            </section>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              padding: "12px 18px",
              borderRadius: 12,
              border: 0,
              background: "#0f766e",
              color: "#fff",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {loading ? (isZh ? "提交中…" : "Submitting...") : isZh ? "保存策略" : "Save Strategy"}
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section style={cardStyle}>
            <h3 style={{ marginTop: 0 }}>{isZh ? "提交预览" : "Submit Preview"}</h3>
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 13,
                lineHeight: 1.6,
                color: "#cbd5e1",
              }}
            >
              {JSON.stringify(previewPayload, null, 2)}
            </pre>
          </section>

          {err && <div style={{ color: "#fda4af" }}>{err}</div>}

          {resp && (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>{isZh ? "后端响应" : "Backend Response"}</h3>
              <pre
                style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 13,
                lineHeight: 1.6,
                color: "#cbd5e1",
              }}
            >
              {JSON.stringify(resp, null, 2)}
              </pre>
            </section>
          )}
        </div>
      </div>
    </form>
  );
}
