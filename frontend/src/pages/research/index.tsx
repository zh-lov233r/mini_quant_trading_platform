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
import type { ResearchExperiment } from "@/types/research";
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
      const run = await startAgentWorkflow(
        WORKFLOWS[mode],
        goal.trim(),
        mode === "experiment" ? { strategyId } : {},
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
const primaryButton: CSSProperties = { padding: "11px 18px", border: 0, borderRadius: 12, background: "#0891b2", color: "white", fontWeight: 800, cursor: "pointer" };
const rowStyle: CSSProperties = { display: "flex", justifyContent: "space-between", gap: 18, alignItems: "center", padding: 16, color: "#e2e8f0", textDecoration: "none", borderRadius: 14, border: "1px solid rgba(100,116,139,.28)", background: "rgba(15,23,42,.55)" };
const modeStyle = (active: boolean): CSSProperties => ({ padding: 16, textAlign: "left", color: active ? "#ecfeff" : "#cbd5e1", borderRadius: 14, border: `1px solid ${active ? "#0e7490" : "#334155"}`, background: active ? "rgba(8,145,178,.18)" : "rgba(15,23,42,.58)", cursor: "pointer", font: "inherit" });
