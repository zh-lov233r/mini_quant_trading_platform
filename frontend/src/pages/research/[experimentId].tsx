import type { CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";

import { cancelAgentWorkflow } from "@/api/agentops";
import { getResearchExperiment, listExperimentTrials } from "@/api/research";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import type { ExperimentTrial, ResearchExperiment } from "@/types/research";

const TERMINAL = new Set(["completed", "partially_failed", "failed", "cancelled", "data_changed"]);

export default function ResearchExperimentPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const id = typeof router.query.experimentId === "string" ? router.query.experimentId : "";
  const [experiment, setExperiment] = useState<ResearchExperiment | null>(null);
  const [trials, setTrials] = useState<ExperimentTrial[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const [nextExperiment, nextTrials] = await Promise.all([
        getResearchExperiment(id),
        listExperimentTrials(id),
      ]);
      setExperiment(nextExperiment);
      setTrials(nextTrials);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [id]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (!experiment || !TERMINAL.has(experiment.status)) void refresh();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [experiment, refresh]);

  async function cancelExperiment() {
    if (!experiment) return;
    try {
      await cancelAgentWorkflow(experiment.workflowRunId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const report = experiment?.report || {};
  const termination = report.termination;
  const tokenUsage = report.tokenUsage || {};
  const counts = report.counts || experiment?.progress || {};
  const bestTrial = report.bestOutOfSampleTrial;

  return (
    <AppShell
      title={experiment ? String(experiment.spec.name || "Research experiment") : (isZh ? "研究实验" : "Research experiment")}
      subtitle={experiment ? String(experiment.spec.hypothesis || experiment.id) : id}
      actions={experiment ? <Link href={`/agent-runs/${experiment.workflowRunId}`} style={linkButton}>{isZh ? "查看 Agent 运行" : "View agent run"}</Link> : undefined}
    >
      {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}
      {!experiment ? <p>{isZh ? "加载中…" : "Loading…"}</p> : (
        <>
          <section style={panelStyle}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
              <div><Badge>{experiment.status}</Badge><span style={{ marginLeft: 12 }}>{Number(experiment.progress.completed || 0)} / {Number(experiment.progress.total || trials.length)}</span></div>
              {!TERMINAL.has(experiment.status) ? (
                <button style={dangerButton} onClick={() => void cancelExperiment()}>{isZh ? "取消实验" : "Cancel experiment"}</button>
              ) : null}
            </div>
            {experiment.errorCode ? <p style={{ color: "#fda4af" }}>{experiment.errorCode}: {experiment.errorMessage}</p> : null}
            {experiment.status === "data_changed" ? <p style={{ color: "#fbbf24" }}>{isZh ? "数据指纹已变化，实验已停止，未混合不同数据版本。" : "The data fingerprint changed. Execution stopped without mixing data versions."}</p> : null}
            {termination?.earlyStopped ? (
              <p style={{ color: "#67e8f9" }}>
                {isZh ? "实验已按自动条件提前停止：" : "Experiment stopped early by policy: "}
                {terminationLabel(termination.reason, isZh)}
              </p>
            ) : null}
          </section>

          <section style={{ ...panelStyle, marginTop: 18 }}>
            <h2 style={{ marginTop: 0 }}>{isZh ? "执行摘要" : "Execution summary"}</h2>
            <div style={summaryGridStyle}>
              <MetricCard label={isZh ? "完成 Trial" : "Completed trials"} value={`${Number(counts.completed || 0)} / ${Number(counts.total || trials.length)}`} />
              <MetricCard label={isZh ? "失败" : "Failed"} value={String(Number(counts.failed || 0))} />
              <MetricCard label={isZh ? "策略停止" : "Termination"} value={terminationLabel(termination?.reason || "running", isZh)} />
              <MetricCard label={isZh ? "Agent tokens" : "Agent tokens"} value={formatInteger(tokenUsage.totalTokens)} />
            </div>
            {bestTrial ? (
              <p style={{ marginBottom: 0 }}>
                {isZh ? "最佳样本外 Trial：" : "Best out-of-sample trial: "}
                {bestTrial.backtestRunId ? (
                  <Link href={`/backtests/${bestTrial.backtestRunId}`} style={{ color: "#67e8f9" }}>
                    {bestTrial.trialId.slice(0, 8)} · {formatMetric(bestTrial.metrics?.total_return)}
                  </Link>
                ) : bestTrial.trialId.slice(0, 8)}
              </p>
            ) : null}
          </section>

          <section style={{ ...panelStyle, marginTop: 18 }}>
            <h2 style={{ marginTop: 0 }}>{isZh ? "Trial 明细" : "Trial details"}</h2>
            <div style={{ overflowX: "auto" }}>
              <table style={tableStyle}>
                <thead><tr><th>#</th><th>Status</th><th>Sample</th><th>Cost</th><th>Window</th><th>Return</th><th>Sharpe</th><th>Backtest</th></tr></thead>
                <tbody>{trials.map((trial) => (
                  <tr key={trial.id}>
                    <td>{trial.ordinal}</td><td>{trial.status}</td><td>{trial.sampleKind}</td><td>{trial.costScenario}</td>
                    <td>{trial.windowStart} → {trial.windowEnd}</td>
                    <td>{formatMetric(trial.metrics.total_return)}</td><td>{formatMetric(trial.metrics.sharpe)}</td>
                    <td>{trial.backtestRunId ? <Link href={`/backtests/${trial.backtestRunId}`} style={{ color: "#67e8f9" }}>{trial.backtestRunId.slice(0, 8)}</Link> : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </section>

          <section style={{ ...panelStyle, marginTop: 18 }}>
            <h2 style={{ marginTop: 0 }}>{isZh ? "稳健性报告" : "Robustness report"}</h2>
            {Object.keys(report).length ? (
              <>
                {report.disclaimer ? <p style={{ color: "#fbbf24" }}>{report.disclaimer}</p> : null}
                <details>
                  <summary style={{ cursor: "pointer", fontWeight: 800 }}>{isZh ? "查看完整确定性报告" : "View full deterministic report"}</summary>
                  <pre style={preStyle}>{JSON.stringify(report, null, 2)}</pre>
                </details>
              </>
            ) : <p style={{ color: "#94a3b8" }}>{isZh ? "报告将在实验结束或自动停止后生成。" : "The report is generated after completion or an automatic stop."}</p>}
          </section>

          <details style={{ ...panelStyle, marginTop: 18 }}>
            <summary style={{ cursor: "pointer", fontWeight: 800 }}>{isZh ? "实验规格与数据清单" : "Specification and data manifest"}</summary>
            <pre style={preStyle}>{JSON.stringify({ spec: experiment.spec, runManifest: experiment.runManifest }, null, 2)}</pre>
          </details>
        </>
      )}
    </AppShell>
  );
}

function formatMetric(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function formatInteger(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "—";
}

function terminationLabel(reason: string, isZh: boolean) {
  const labels: Record<string, [string, string]> = {
    running: ["运行中", "Running"],
    all_trials_completed: ["全部 Trial 完成", "All trials completed"],
    time_limit_reached: ["达到运行时间上限", "Time limit reached"],
    token_budget_reached: ["达到 token 上限", "Token budget reached"],
    target_reached: ["达到目标指标", "Target metric reached"],
  };
  const label = labels[reason];
  return label ? label[isZh ? 0 : 1] : reason;
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return <div style={metricCardStyle}><span style={{ color: "#94a3b8" }}>{label}</span><strong style={{ marginTop: 8, fontSize: 20 }}>{value}</strong></div>;
}

const panelStyle: CSSProperties = { padding: 22, borderRadius: 20, border: "1px solid rgba(100,116,139,.35)", background: "rgba(8,15,24,.8)" };
const linkButton: CSSProperties = { padding: "10px 16px", borderRadius: 10, background: "#0891b2", color: "white", textDecoration: "none", fontWeight: 800 };
const dangerButton: CSSProperties = { padding: "10px 16px", border: "1px solid #be123c", borderRadius: 10, background: "rgba(159,18,57,.2)", color: "#fecdd3", fontWeight: 800, cursor: "pointer" };
const tableStyle: CSSProperties = { width: "100%", borderCollapse: "collapse", textAlign: "left", lineHeight: 1.7 };
const preStyle: CSSProperties = { overflow: "auto", padding: 14, borderRadius: 10, background: "#020617", color: "#bae6fd", fontSize: 12, lineHeight: 1.55 };
const summaryGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 };
const metricCardStyle: CSSProperties = { display: "flex", flexDirection: "column", padding: 14, borderRadius: 12, border: "1px solid rgba(100,116,139,.3)", background: "rgba(15,23,42,.62)" };
