import React, { useEffect, useMemo, useState } from "react";

import { createStrategy, getStrategyCatalog } from "@/api/strategies";
import type {
  StrategyCatalogItem,
  StrategyCreate,
  StrategyOut,
  StrategyStatus,
  StrategyType,
} from "@/types/strategy";

export default function StrategyForm() {
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [name, setName] = useState("Trend_EMA15_SMA200");
  const [description, setDescription] = useState("双均线趋势策略");
  const [strategyType, setStrategyType] = useState<StrategyType>("trend");
  const [status, setStatus] = useState<StrategyStatus>("draft");
  const [fastKind, setFastKind] = useState<"ema" | "sma">("ema");
  const [fastWindow, setFastWindow] = useState(15);
  const [slowKind, setSlowKind] = useState<"ema" | "sma">("sma");
  const [slowWindow, setSlowWindow] = useState(200);
  const [volMul, setVolMul] = useState(1.5);
  const [atrMul, setAtrMul] = useState(2.0);
  const [symbols, setSymbols] = useState("AAPL,MSFT,NVDA");
  const [maxPositions, setMaxPositions] = useState(10);
  const [positionSizePct, setPositionSizePct] = useState(0.1);
  const [rebalance, setRebalance] = useState("daily");
  const [runAt, setRunAt] = useState("close");
  const [rawJson, setRawJson] = useState("{\n  \"rules\": []\n}");
  const [resp, setResp] = useState<StrategyOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getStrategyCatalog()
      .then((items) => {
        if (!cancelled) {
          setCatalog(items);
        }
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setCatalogError(error.message || "无法加载策略模板");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (strategyType === "trend" || catalog.length === 0) {
      return;
    }
    const item = catalog.find((entry) => entry.strategy_type === strategyType);
    if (item) {
      setRawJson(JSON.stringify(item.defaults, null, 2));
    }
  }, [catalog, strategyType]);

  const trendParams = useMemo(
    () => ({
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
        symbols: symbols
          .split(",")
          .map((item) => item.trim().toUpperCase())
          .filter(Boolean),
        selection_mode: "manual",
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
    }),
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
  }, [description, name, rawJson, status, strategyType, trendParams]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setResp(null);
    setLoading(true);

    try {
      if (!name.trim()) {
        throw new Error("策略名不能为空");
      }

      let payload: StrategyCreate;
      if (strategyType === "trend") {
        if (!(Number(fastWindow) > 0)) throw new Error("短周期必须 > 0");
        if (!(Number(slowWindow) > 0)) throw new Error("长周期必须 > 0");
        if (!(Number(volMul) > 0)) throw new Error("成交量过滤倍数必须 > 0");
        if (!(Number(atrMul) > 0)) throw new Error("ATR 乘数必须 > 0");
        if (!(Number(maxPositions) > 0)) throw new Error("最大持仓数必须 > 0");
        if (!(Number(positionSizePct) > 0 && Number(positionSizePct) <= 1)) {
          throw new Error("单票仓位比例必须在 (0, 1] 之间");
        }
        payload = {
          name: name.trim(),
          description: description.trim(),
          strategy_type: strategyType,
          status,
          params: trendParams,
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

      const idem = (crypto as any)?.randomUUID?.() || String(Date.now());
      const data = await createStrategy(payload, idem);
      setResp(data);
    } catch (error: any) {
      setErr(error?.message || "提交失败");
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
    padding: 10,
    border: "1px solid #d1d5db",
    borderRadius: 8,
    fontSize: 14,
  };
  const cardStyle: React.CSSProperties = {
    padding: 18,
    border: "1px solid #e5e7eb",
    borderRadius: 16,
    background: "#fff",
    boxShadow: "0 8px 30px rgba(15, 23, 42, 0.06)",
  };

  return (
    <form
      onSubmit={submit}
      style={{
        maxWidth: 960,
        margin: "32px auto",
        padding: "0 16px 48px",
        fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif",
        color: "#111827",
      }}
    >
      <div
        style={{
          marginBottom: 20,
          padding: 24,
          borderRadius: 20,
          background:
            "linear-gradient(135deg, rgba(15,118,110,0.12), rgba(14,165,233,0.08))",
        }}
      >
        <h2 style={{ margin: "0 0 10px", fontSize: 28 }}>创建策略</h2>
        <p style={{ margin: 0, color: "#4b5563", lineHeight: 1.6 }}>
          前端填写策略定义，后端会统一标准化后落库，策略引擎直接消费标准化配置。
        </p>
      </div>

      {catalogError && (
        <div style={{ color: "crimson", marginBottom: 16 }}>{catalogError}</div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section style={cardStyle}>
            <div style={boxStyle}>
              <label>策略名</label>
              <input
                style={inputStyle}
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>

            <div style={boxStyle}>
              <label>策略说明</label>
              <textarea
                style={{ ...inputStyle, minHeight: 90, resize: "vertical" }}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="例如：收盘后扫描趋势股，第二天开盘前生成调仓建议"
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div style={boxStyle}>
                <label>策略类型</label>
                <select
                  style={inputStyle}
                  value={strategyType}
                  onChange={(e) => setStrategyType(e.target.value as StrategyType)}
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
                <label>状态</label>
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
              <h3 style={{ marginTop: 0 }}>趋势参数</h3>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>快线类型</label>
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
                  <label>快线周期</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={fastWindow}
                    onChange={(e) => setFastWindow(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>慢线类型</label>
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
                  <label>慢线周期</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={slowWindow}
                    onChange={(e) => setSlowWindow(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>成交量过滤倍数</label>
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
                  <label>ATR 乘数</label>
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
                <label>股票池</label>
                <input
                  style={inputStyle}
                  value={symbols}
                  onChange={(e) => setSymbols(e.target.value)}
                  placeholder="AAPL,MSFT,NVDA"
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
                <div style={boxStyle}>
                  <label>最大持仓数</label>
                  <input
                    type="number"
                    min={1}
                    style={inputStyle}
                    value={maxPositions}
                    onChange={(e) => setMaxPositions(Number(e.target.value))}
                  />
                </div>
                <div style={boxStyle}>
                  <label>单票仓位比例</label>
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
                  <label>调仓频率</label>
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
                <label>运行时机</label>
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
              <h3 style={{ marginTop: 0 }}>高级 JSON 配置</h3>
              <p style={{ marginTop: 0, color: "#6b7280", lineHeight: 1.6 }}>
                非趋势策略先以 JSON/DSL 形式落库，当前后端支持存储和查询，等专门 evaluator
                接好后即可执行。
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
            {loading ? "提交中…" : "保存策略"}
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <section style={cardStyle}>
            <h3 style={{ marginTop: 0 }}>提交预览</h3>
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              {JSON.stringify(previewPayload, null, 2)}
            </pre>
          </section>

          {err && <div style={{ color: "crimson" }}>{err}</div>}

          {resp && (
            <section style={cardStyle}>
              <h3 style={{ marginTop: 0 }}>后端响应</h3>
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  fontSize: 13,
                  lineHeight: 1.6,
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
