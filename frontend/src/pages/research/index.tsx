import type { CSSProperties, FormEvent } from "react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/router";

import { startAgentWorkflow } from "@/api/agentops";
import { listResearchExperiments } from "@/api/research";
import { getStrategyCatalog, getStrategyFeatureSupport } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import { WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { ResearchExperiment } from "@/types/research";
import type { StrategyCatalogItem, StrategyFeatureSupport } from "@/types/strategy";

const WORKFLOWS = {
  category: "Quant Engine Category Research",
  algorithm: "Quant New Algorithm Research",
} as const;
type Mode = keyof typeof WORKFLOWS;

export default function ResearchHomePage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [mode, setMode] = useState<Mode>("category");
  const [goal, setGoal] = useState("");
  const [items, setItems] = useState<ResearchExperiment[]>([]);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [featureSupport, setFeatureSupport] = useState<StrategyFeatureSupport | null>(null);
  const [strategyType, setStrategyType] = useState("");
  const [maxRounds, setMaxRounds] = useState(3);
  const [maxTrials, setMaxTrials] = useState(48);
  const [maxDurationMinutes, setMaxDurationMinutes] = useState(60);
  const [tokenBudget, setTokenBudget] = useState(80_000);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!router.isReady) return;
    const requestedMode = Array.isArray(router.query.mode) ? router.query.mode[0] : router.query.mode;
    if (requestedMode === "category" || requestedMode === "algorithm") setMode(requestedMode);
  }, [router.isReady, router.query.mode]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listResearchExperiments(), getStrategyCatalog(), getStrategyFeatureSupport()])
      .then(([experiments, categories, support]) => {
        if (cancelled) return;
        const executable = categories.filter((item) => item.engine_ready);
        setItems(experiments);
        setCatalog(executable);
        setFeatureSupport(support);
        setStrategyType((current) => current || executable[0]?.strategy_type || "");
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  const selectedCategory = useMemo(
    () => catalog.find((item) => item.strategy_type === strategyType),
    [catalog, strategyType],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim() || (mode === "category" && !strategyType)) return;
    setSubmitting(true);
    setError(null);
    try {
      const inputs = mode === "category" ? {
        strategyType,
        strategyDefaults: selectedCategory?.defaults ?? {},
        featureSupport: strategyType === "trend" ? featureSupport?.trend ?? null : null,
        maxRounds,
        maxTrials,
        maxDurationSeconds: Math.round(maxDurationMinutes * 60),
        tokenBudget: Math.round(tokenBudget),
      } : {};
      const run = await startAgentWorkflow(WORKFLOWS[mode], goal.trim(), inputs);
      await router.push(`/agent-runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <AppShell
      title={isZh ? "Agent 研究工作台" : "Agent Research Workspace"}
      subtitle={isZh
        ? "选择已有引擎大类进行多轮 Pareto 研究，或为没有 handler 的算法交付 Draft PR。"
        : "Run multi-round Pareto research on an existing engine category, or deliver a Draft PR for an algorithm without a handler."}
      actions={(
        <>
          {router.query.source === "strategy-create" ? (
            <Link href="/strategies/new" style={returnLinkStyle}>
              {isZh ? "返回策略创建" : "Back To Strategy Creation"}
            </Link>
          ) : null}
        </>
      )}
    >
      <WorkspaceDialog
        triggerLabel={isZh ? "创建研究实验" : "Create Research Experiment"}
        title={isZh ? "创建研究实验" : "Create Research Experiment"}
        description={isZh ? "选择研究模式、资源上限和目标后生成待审批提案。" : "Choose the research mode, resource limits, and goal to generate a proposal for approval."}
        size="form"
        triggerTone="primary"
      >
        <div style={modeGridStyle}>
          <ModeCard active={mode === "category"} title={isZh ? "已有引擎大类研究" : "Existing engine category research"} description={isZh ? "只选择策略大类。Agent 自动创建可执行 draft，再由你审批实验。" : "Choose only an engine category. The agent creates an executable draft before experiment approval."} onClick={() => setMode("category")} />
          <ModeCard active={mode === "algorithm"} title={isZh ? "新算法研究" : "New algorithm research"} description={isZh ? "Planner → Codex → Quant Verifier → Draft PR；不会自动合并、部署或回测。" : "Planner → Codex → Quant Verifier → Draft PR; no automatic merge, deploy, or backtest."} onClick={() => setMode("algorithm")} />
        </div>
        <form onSubmit={submit} style={{ marginTop: 20 }}>
          {mode === "category" ? (
            <>
              <label htmlFor="strategy-category" style={labelStyle}>{isZh ? "策略大类" : "Engine category"}</label>
              <select id="strategy-category" value={strategyType} onChange={(event) => setStrategyType(event.target.value)} style={inputStyle}>
                {catalog.map((item) => <option key={item.strategy_type} value={item.strategy_type}>{item.label} · {item.strategy_type}</option>)}
              </select>
              {selectedCategory ? <div style={categorySummaryStyle}><strong>{selectedCategory.description}</strong><pre style={compactPreStyle}>{JSON.stringify({ signal: selectedCategory.defaults.signal, risk: selectedCategory.defaults.risk }, null, 2)}</pre></div> : null}
              <div style={resourceGridStyle}>
                <NumberField label={isZh ? "最大轮数（1–5）" : "Max rounds (1–5)"} value={maxRounds} min={1} max={5} onChange={setMaxRounds} />
                <NumberField label={isZh ? "实际 Backtest 上限（4–100）" : "Actual backtest limit (4–100)"} value={maxTrials} min={4} max={100} onChange={setMaxTrials} />
                <NumberField label={isZh ? "最长时间（分钟）" : "Time limit (minutes)"} value={maxDurationMinutes} min={1} max={10080} onChange={setMaxDurationMinutes} />
                <NumberField label={isZh ? "Agent token 上限" : "Agent token budget"} value={tokenBudget} min={1000} max={10000000} step={1000} onChange={setTokenBudget} />
              </div>
              <p style={noticeStyle}>{isZh ? "默认 3 轮 / 48 个实际回测；目标、自动创建的 draft 和首轮候选会在实验审批前展示。" : "Defaults: 3 rounds / 48 actual backtests. Objectives, the auto-created draft, and first-round candidates appear before approval."}</p>
            </>
          ) : <p style={noticeStyle}>{isZh ? "Draft PR 合并部署并注册为 engine-ready 后，才能从“大类研究”发起参数实验。" : "After the Draft PR is merged, deployed, and registered as engine-ready, use category research for parameter experiments."}</p>}
          <label htmlFor="research-goal" style={labelStyle}>{isZh ? "研究目标与约束" : "Research goal and constraints"}</label>
          <textarea id="research-goal" value={goal} onChange={(event) => setGoal(event.target.value)} rows={7} placeholder={mode === "category" ? (isZh ? "例如：在 AAPL、MSFT 上研究低回撤趋势策略，明确样本内外窗口与成本压力。" : "For example: research a low-drawdown trend strategy on AAPL and MSFT with explicit windows and cost stress.") : (isZh ? "描述没有现成 handler 的算法、验收标准和回归测试。" : "Describe the algorithm without an existing handler, acceptance criteria, and regression tests.")} style={inputStyle} />
          <div style={actionRowStyle}><span style={{ color: "#94a3b8" }}>{isZh ? "Agent 无权激活策略、创建 allocation 或触发订单。" : "The agent cannot activate strategies, allocate capital, or submit orders."}</span><button disabled={submitting || !goal.trim() || (mode === "category" && !strategyType)} style={primaryButton}>{submitting ? (isZh ? "正在启动…" : "Starting…") : (isZh ? "生成研究提案" : "Generate research proposal")}</button></div>
        </form>
        {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}
      </WorkspaceDialog>
      <section style={{ ...panelStyle, marginTop: 20 }}>
        <h2 style={{ marginTop: 0 }}>{isZh ? "历史与当前实验" : "Historical and current experiments"}</h2>
        {loading ? <p>{isZh ? "加载中…" : "Loading…"}</p> : null}
        {!loading && items.length === 0 ? <p style={{ color: "#94a3b8" }}>{isZh ? "尚无实验。" : "No experiments yet."}</p> : null}
        <div style={{ display: "grid", gap: 12 }}>{items.map((item) => <Link key={item.id} href={`/research/${item.id}`} style={rowStyle}><div><strong>{String(item.spec.name || item.id)}</strong><div style={{ marginTop: 7, color: "#94a3b8" }}>{String(item.spec.hypothesis || "")}</div></div><div style={{ display: "flex", gap: 10, alignItems: "center" }}><Badge>{item.status}</Badge><span>{Number(item.progress.completed || 0)}/{Number(item.progress.total || 0)}</span></div></Link>)}</div>
      </section>
    </AppShell>
  );
}

function ModeCard({ active, title, description, onClick }: { active: boolean; title: string; description: string; onClick: () => void }) { return <button type="button" onClick={onClick} style={modeStyle(active)}><strong>{title}</strong><span style={{ display: "block", marginTop: 8, opacity: 0.74, lineHeight: 1.5 }}>{description}</span></button>; }
function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void }) { return <label style={labelStyle}>{label}<input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} style={{ ...inputStyle, marginTop: 8 }} /></label>; }

const panelStyle: CSSProperties = { padding: 22, borderRadius: 22, border: "1px solid rgba(100,116,139,.35)", background: "rgba(8,15,24,.8)" };
const modeGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 };
const labelStyle: CSSProperties = { display: "block", marginBottom: 12, fontWeight: 700 };
const inputStyle: CSSProperties = { boxSizing: "border-box", width: "100%", padding: 12, color: "#e2e8f0", background: "#07111c", border: "1px solid #334155", borderRadius: 12, resize: "vertical", font: "inherit", lineHeight: 1.5 };
const categorySummaryStyle: CSSProperties = { marginTop: 12, padding: 14, border: "1px solid rgba(14,116,144,.55)", borderRadius: 12, background: "rgba(8,145,178,.08)" };
const compactPreStyle: CSSProperties = { maxHeight: 220, overflow: "auto", marginBottom: 0, padding: 10, borderRadius: 8, background: "#020617", color: "#bae6fd", fontSize: 11 };
const resourceGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginTop: 18 };
const noticeStyle: CSSProperties = { padding: 12, borderRadius: 10, color: "#bae6fd", background: "rgba(14,116,144,.12)", lineHeight: 1.6 };
const actionRowStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginTop: 14, flexWrap: "wrap" };
const primaryButton: CSSProperties = { padding: "11px 18px", border: 0, borderRadius: 12, background: "#0891b2", color: "white", fontWeight: 800, cursor: "pointer" };
const rowStyle: CSSProperties = { display: "flex", justifyContent: "space-between", gap: 18, alignItems: "center", padding: 16, color: "#e2e8f0", textDecoration: "none", borderRadius: 14, border: "1px solid rgba(100,116,139,.28)", background: "rgba(15,23,42,.55)" };
const modeStyle = (active: boolean): CSSProperties => ({ padding: 18, textAlign: "left", color: active ? "#ecfeff" : "#cbd5e1", borderRadius: 14, border: `1px solid ${active ? "#0e7490" : "#334155"}`, background: active ? "rgba(8,145,178,.18)" : "rgba(15,23,42,.58)", cursor: "pointer", font: "inherit" });
const returnLinkStyle: CSSProperties = { padding: "11px 16px", borderRadius: 14, border: "1px solid rgba(148,163,184,.2)", background: "rgba(15,23,42,.72)", color: "#dbeafe", textDecoration: "none", fontWeight: 750 };
