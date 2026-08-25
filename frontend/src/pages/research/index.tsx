import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";

import { startAgentWorkflow } from "@/api/agentops";
import { listResearchExperiments } from "@/api/research";
import { listStrategies } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import type { ExperimentStopPolicy, ResearchExperiment, ResearchTargetMetric } from "@/types/research";
import type { StrategyOut } from "@/types/strategy";

const WORKFLOWS = {
  parameter: "Quant Parameter Strategy",
  code: "Quant Strategy Code Development",
  experiment: "Quant Research Experiment",
} as const;

type Mode = keyof typeof WORKFLOWS;

export default function ResearchHomePage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [mode, setMode] = useState<Mode>("experiment");
  const [goal, setGoal] = useState("");
  const [items, setItems] = useState<ResearchExperiment[]>([]);
  const [strategies, setStrategies] = useState<StrategyOut[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [durationEnabled, setDurationEnabled] = useState(true);
  const [maxDurationMinutes, setMaxDurationMinutes] = useState(30);
  const [tokenEnabled, setTokenEnabled] = useState(true);
  const [tokenBudget, setTokenBudget] = useState(50_000);
  const [targetEnabled, setTargetEnabled] = useState(false);
  const [targetMetric, setTargetMetric] = useState<ResearchTargetMetric>("total_return");
  const [targetOperator, setTargetOperator] = useState<"gte" | "lte">("gte");
  const [targetValue, setTargetValue] = useState(0.05);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listResearchExperiments(), listStrategies()])
      .then(([experiments, strategyItems]) => {
        if (cancelled) return;
        const engineReady = strategyItems.filter((item) => item.engine_ready);
        setItems(experiments);
        setStrategies(engineReady);
        setStrategyId((current) => current || engineReady[0]?.id || "");
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const copy = useMemo(
    () => ({
      parameter: isZh
        ? "描述你希望创建的参数策略、股票范围和风险约束。Agent 只会创建 draft。"
        : "Describe the parameter strategy, universe, and risk limits. The agent can only create a draft.",
      code: isZh
        ? "描述新算法与验收标准。Coder 只交付 Draft PR，部署前不能用于实验。"
        : "Describe the algorithm and acceptance criteria. The coder only delivers a Draft PR.",
      experiment: isZh
        ? "描述研究假设、基础策略、样本内外日期、参数范围、标的和成本压力。"
        : "Describe the hypothesis, base strategy, date windows, parameter grid, universe, and cost stress.",
    }),
    [isZh],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const stopPolicy: ExperimentStopPolicy = {};
      if (mode === "experiment") {
        if (durationEnabled) stopPolicy.maxDurationSeconds = Math.round(maxDurationMinutes * 60);
        if (tokenEnabled) stopPolicy.tokenBudget = Math.round(tokenBudget);
        if (targetEnabled) {
          stopPolicy.targetMetric = {
            metric: targetMetric,
            operator: targetOperator,
            value: targetValue,
            sampleKind: "out_of_sample",
            costScenario: "base",
          };
        }
        if (!Object.keys(stopPolicy).length) {
          throw new Error(isZh ? "请至少启用一个自动停止条件。" : "Enable at least one automatic stop condition.");
        }
      }
      const run = await startAgentWorkflow(
        WORKFLOWS[mode],
        goal.trim(),
        mode === "experiment" ? { strategyId, stopPolicy } : {},
      );
      await router.push(`/agent-runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <AppShell
      title={isZh ? "Agent 研究工作台" : "Agent Research Workspace"}
      subtitle={
        isZh
          ? "从自然语言开始，经结构化校验和人工审批，创建草稿策略或可恢复的稳健性实验。"
          : "Start from natural language, then validate and approve a draft strategy or resumable robustness experiment."
      }
    >
      <section style={panelStyle}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
          {(Object.keys(WORKFLOWS) as Mode[]).map((item) => (
            <button key={item} type="button" onClick={() => setMode(item)} style={modeStyle(mode === item)}>
              <strong>{modeLabel(item, isZh)}</strong>
              <span style={{ display: "block", marginTop: 8, opacity: 0.72, lineHeight: 1.5 }}>{copy[item]}</span>
            </button>
          ))}
        </div>
        <form onSubmit={submit} style={{ marginTop: 20 }}>
          {mode === "experiment" ? (
            <div style={{ marginBottom: 14 }}>
              <label htmlFor="research-strategy" style={labelStyle}>
                {isZh ? "基础策略（已部署且 engine-ready）" : "Base strategy (deployed and engine-ready)"}
              </label>
              <select
                id="research-strategy"
                value={strategyId}
                onChange={(event) => setStrategyId(event.target.value)}
                style={inputStyle}
              >
                {strategies.map((strategy) => (
                  <option key={strategy.id} value={strategy.id}>
                    {strategy.name} · v{strategy.version} · {strategy.strategy_type}
                  </option>
                ))}
              </select>
              {!loading && strategies.length === 0 ? (
                <p style={{ marginBottom: 0, color: "#fda4af" }}>
                  {isZh ? "没有可研究的 engine-ready 策略。" : "No engine-ready strategy is available for research."}
                </p>
              ) : null}
              <div style={{ ...stopPolicyStyle, marginTop: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                  <strong>{isZh ? "自动停止条件" : "Automatic stop conditions"}</strong>
                  <span style={{ color: "#94a3b8" }}>
                    {isZh ? "任意一个已启用条件命中即停止领取新 Trial" : "Any enabled condition stops new trial claims"}
                  </span>
                </div>
                <div style={stopGridStyle}>
                  <label style={stopOptionStyle}>
                    <span><input type="checkbox" checked={durationEnabled} onChange={(event) => setDurationEnabled(event.target.checked)} /> {isZh ? "最长运行时间" : "Maximum duration"}</span>
                    <span style={fieldRowStyle}>
                      <input type="number" min={1} max={10080} value={maxDurationMinutes} disabled={!durationEnabled} onChange={(event) => setMaxDurationMinutes(Number(event.target.value))} style={smallInputStyle} />
                      <span>{isZh ? "分钟" : "minutes"}</span>
                    </span>
                  </label>
                  <label style={stopOptionStyle}>
                    <span><input type="checkbox" checked={tokenEnabled} onChange={(event) => setTokenEnabled(event.target.checked)} /> {isZh ? "Agent token 上限" : "Agent token budget"}</span>
                    <input type="number" min={1000} max={10000000} step={1000} value={tokenBudget} disabled={!tokenEnabled} onChange={(event) => setTokenBudget(Number(event.target.value))} style={smallInputStyle} />
                  </label>
                  <label style={stopOptionStyle}>
                    <span><input type="checkbox" checked={targetEnabled} onChange={(event) => setTargetEnabled(event.target.checked)} /> {isZh ? "样本外目标" : "Out-of-sample target"}</span>
                    <span style={{ ...fieldRowStyle, flexWrap: "wrap" }}>
                      <select value={targetMetric} disabled={!targetEnabled} onChange={(event) => setTargetMetric(event.target.value as ResearchTargetMetric)} style={smallInputStyle}>
                        <option value="total_return">total return</option>
                        <option value="sharpe">Sharpe</option>
                        <option value="max_drawdown">max drawdown</option>
                        <option value="excess_return">excess return</option>
                      </select>
                      <select value={targetOperator} disabled={!targetEnabled} onChange={(event) => setTargetOperator(event.target.value as "gte" | "lte")} style={smallInputStyle}>
                        <option value="gte">≥</option>
                        <option value="lte">≤</option>
                      </select>
                      <input type="number" step="any" value={targetValue} disabled={!targetEnabled} onChange={(event) => setTargetValue(Number(event.target.value))} style={smallInputStyle} />
                    </span>
                    <small style={{ color: "#94a3b8" }}>{isZh ? "固定使用 out_of_sample / base 场景；收益率使用小数，例如 0.05 = 5%。" : "Uses out_of_sample / base; returns are decimals, for example 0.05 = 5%."}</small>
                  </label>
                </div>
              </div>
            </div>
          ) : null}
          <label style={labelStyle}>{isZh ? "研发目标" : "Research goal"}</label>
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={7}
            placeholder={copy[mode]}
            style={inputStyle}
          />
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 14, flexWrap: "wrap" }}>
            <span style={{ color: "#94a3b8", lineHeight: 1.6 }}>
              {isZh ? "创建资源前会停在审批节点；Agent 没有订单或组合激活权限。" : "The run pauses for approval before creation and has no order or activation permission."}
            </span>
            <button
              disabled={submitting || !goal.trim() || (mode === "experiment" && !strategyId)}
              style={primaryButton}
            >
              {submitting ? (isZh ? "正在启动…" : "Starting…") : (isZh ? "生成提案" : "Generate proposal")}
            </button>
          </div>
        </form>
        {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}
      </section>

      <section style={{ ...panelStyle, marginTop: 20 }}>
        <h2 style={{ marginTop: 0 }}>{isZh ? "研究实验" : "Research experiments"}</h2>
        {loading ? <p>{isZh ? "加载中…" : "Loading…"}</p> : null}
        {!loading && items.length === 0 ? <p style={{ color: "#94a3b8" }}>{isZh ? "尚无实验。" : "No experiments yet."}</p> : null}
        <div style={{ display: "grid", gap: 12 }}>
          {items.map((item) => (
            <Link key={item.id} href={`/research/${item.id}`} style={rowStyle}>
              <div>
                <strong>{String(item.spec.name || item.id)}</strong>
                <div style={{ marginTop: 7, color: "#94a3b8" }}>{String(item.spec.hypothesis || "")}</div>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <Badge>{item.status}</Badge>
                <span>{Number(item.progress.completed || 0)}/{Number(item.progress.total || 0)}</span>
              </div>
            </Link>
          ))}
        </div>
      </section>
    </AppShell>
  );
}

function modeLabel(mode: Mode, isZh: boolean) {
  if (mode === "parameter") return isZh ? "参数策略" : "Parameter strategy";
  if (mode === "code") return isZh ? "新代码策略" : "New code strategy";
  return isZh ? "研究实验" : "Research experiment";
}

const panelStyle: CSSProperties = { padding: 22, borderRadius: 22, border: "1px solid rgba(100,116,139,.35)", background: "rgba(8,15,24,.8)" };
const labelStyle: CSSProperties = { display: "block", marginBottom: 9, fontWeight: 700 };
const inputStyle: CSSProperties = { boxSizing: "border-box", width: "100%", padding: 14, color: "#e2e8f0", background: "#07111c", border: "1px solid #334155", borderRadius: 14, resize: "vertical", font: "inherit", lineHeight: 1.6 };
const stopPolicyStyle: CSSProperties = { padding: 16, border: "1px solid rgba(14,116,144,.65)", borderRadius: 14, background: "rgba(8,145,178,.08)" };
const stopGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginTop: 14 };
const stopOptionStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 10, padding: 12, border: "1px solid rgba(100,116,139,.3)", borderRadius: 12 };
const fieldRowStyle: CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const smallInputStyle: CSSProperties = { minWidth: 0, maxWidth: "100%", padding: "8px 10px", color: "#e2e8f0", background: "#07111c", border: "1px solid #334155", borderRadius: 9, font: "inherit" };
const primaryButton: CSSProperties = { padding: "11px 18px", border: 0, borderRadius: 12, background: "#0891b2", color: "white", fontWeight: 800, cursor: "pointer" };
const rowStyle: CSSProperties = { display: "flex", justifyContent: "space-between", gap: 18, alignItems: "center", padding: 16, color: "#e2e8f0", textDecoration: "none", borderRadius: 14, border: "1px solid rgba(100,116,139,.28)", background: "rgba(15,23,42,.55)" };
const modeStyle = (active: boolean): CSSProperties => ({ padding: 16, textAlign: "left", color: active ? "#ecfeff" : "#cbd5e1", borderRadius: 14, border: `1px solid ${active ? "#0e7490" : "#334155"}`, background: active ? "rgba(8,145,178,.18)" : "rgba(15,23,42,.58)", cursor: "pointer", font: "inherit" });
