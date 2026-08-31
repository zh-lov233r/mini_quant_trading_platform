import type { CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useState } from "react";

import { cancelAgentWorkflow } from "@/api/agentops";
import {
  getResearchExperiment,
  listExperimentChildren,
  listExperimentCandidates,
  listExperimentRounds,
  listExperimentTrials,
  researchArtifactUrl,
} from "@/api/research";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogStack as ContextStack, DialogStat as ContextStat, DialogStats as ContextStats, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { DenseDataTable } from "@/components/workspace/DenseDataTable";
import { useI18n } from "@/i18n/provider";
import type {
  ExperimentCandidate,
  ExperimentRound,
  ExperimentTrial,
  ResearchExperiment,
} from "@/types/research";

const TERMINAL = new Set(["completed", "partially_failed", "failed", "cancelled", "data_changed"]);

export default function ResearchExperimentPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const id = typeof router.query.experimentId === "string" ? router.query.experimentId : "";
  const [experiment, setExperiment] = useState<ResearchExperiment | null>(null);
  const [trials, setTrials] = useState<ExperimentTrial[]>([]);
  const [rounds, setRounds] = useState<ExperimentRound[]>([]);
  const [candidates, setCandidates] = useState<ExperimentCandidate[]>([]);
  const [children, setChildren] = useState<ResearchExperiment[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!id) return;
    try {
      const [nextExperiment, nextTrials, nextRounds, nextCandidates, nextChildren] = await Promise.all([
        getResearchExperiment(id),
        listExperimentTrials(id),
        listExperimentRounds(id),
        listExperimentCandidates(id),
        listExperimentChildren(id),
      ]);
      setExperiment(nextExperiment);
      setTrials(nextTrials);
      setRounds(nextRounds);
      setCandidates(nextCandidates);
      setChildren(nextChildren);
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
  const isEffectivenessStudy = experiment?.studyKind === "support_resistance_effectiveness_v2";
  const finalCandidates = Array.isArray(report.finalCandidates)
    ? report.finalCandidates.map(asRecord)
    : [];
  const reportArtifacts = asRecord(asRecord(experiment?.runManifest).reportArtifacts);
  const artifactFiles = asRecord(reportArtifacts.files);
  const trialColumns = useMemo(() => [
    { id: "ordinal", header: "#", accessor: (trial: ExperimentTrial) => trial.ordinal, sortable: true, width: 70 },
    { id: "status", header: "Status", accessor: (trial: ExperimentTrial) => trial.status, sortable: true, width: 130 },
    { id: "sample", header: "Sample", accessor: (trial: ExperimentTrial) => trial.sampleKind, sortable: true, width: 120 },
    { id: "cost", header: "Cost", accessor: (trial: ExperimentTrial) => trial.costScenario, sortable: true, width: 120 },
    { id: "window", header: "Window", accessor: (trial: ExperimentTrial) => `${trial.windowStart} → ${trial.windowEnd}`, width: 230 },
    { id: "return", header: "Return", accessor: (trial: ExperimentTrial) => trial.metrics.total_return, cell: (value: unknown) => formatMetric(value), sortable: true, width: 110 },
    { id: "sharpe", header: "Sharpe", accessor: (trial: ExperimentTrial) => trial.metrics.sharpe, cell: (value: unknown) => formatMetric(value), sortable: true, width: 110 },
    { id: "backtest", header: "Backtest", accessor: (trial: ExperimentTrial) => trial.backtestRunId || "", cell: (value: unknown, trial: ExperimentTrial) => trial.backtestRunId ? <Link href={`/backtests/${trial.backtestRunId}`} style={{ color: "#67e8f9" }}>{trial.backtestRunId.slice(0, 8)}</Link> : "—", width: 120 },
  ], []);
  const childColumns = useMemo(() => [
    { id: "phase", header: isZh ? "阶段" : "Phase", accessor: (child: ResearchExperiment) => String(child.spec.validationPhase || child.studyKind), sortable: true, filterable: true, width: 180 },
    { id: "status", header: "Status", accessor: (child: ResearchExperiment) => child.status, sortable: true, filterable: true, width: 130 },
    { id: "progress", header: isZh ? "进度" : "Progress", accessor: (child: ResearchExperiment) => `${Number(child.progress.completed || 0)} / ${Number(child.progress.total || 0)}`, width: 130 },
    { id: "id", header: "ID", accessor: (child: ResearchExperiment) => child.id, cell: (_: unknown, child: ResearchExperiment) => <Link href={`/research/${child.id}`} style={{ color: "#67e8f9" }}>{child.id.slice(0, 8)}</Link>, width: 120 },
  ], [isZh]);
  const candidateColumns = useMemo(() => [
    { id: "rank", header: "Rank", accessor: (candidate: ExperimentCandidate) => candidate.paretoRank ?? Number.MAX_SAFE_INTEGER, cell: (value: unknown) => Number(value) === Number.MAX_SAFE_INTEGER ? "—" : String(value), sortable: true, width: 90 },
    { id: "hash", header: "Hash", accessor: (candidate: ExperimentCandidate) => candidate.paramsHash, cell: (value: unknown) => String(value).slice(0, 8), filterable: true, width: 120 },
    { id: "overrides", header: "Overrides", accessor: (candidate: ExperimentCandidate) => JSON.stringify(candidate.overrides), cell: (value: unknown) => <code>{String(value)}</code>, width: 280 },
    { id: "return", header: "OOS return", accessor: (candidate: ExperimentCandidate) => candidate.aggregateMetrics.oos_total_return, cell: (value: unknown) => formatMetric(value), sortable: true, width: 130 },
    { id: "sharpe", header: "Sharpe", accessor: (candidate: ExperimentCandidate) => candidate.aggregateMetrics.oos_sharpe, cell: (value: unknown) => formatMetric(value), sortable: true, width: 110 },
    { id: "drawdown", header: "Drawdown", accessor: (candidate: ExperimentCandidate) => candidate.aggregateMetrics.oos_max_drawdown, cell: (value: unknown) => formatMetric(value), sortable: true, width: 130 },
    { id: "draft", header: isZh ? "已保存 draft" : "Saved draft", accessor: (candidate: ExperimentCandidate) => candidate.promotedStrategyId || "", cell: (_: unknown, candidate: ExperimentCandidate) => candidate.promotedStrategyId ? <Link href={`/strategies/${candidate.promotedStrategyId}`} style={{ color: "#67e8f9" }}>{candidate.promotedStrategyId.slice(0, 8)}</Link> : "—", width: 150 },
  ], [isZh]);

  return (
    <AppShell
      title={experiment ? String(experiment.spec.name || "Research experiment") : (isZh ? "研究实验" : "Research experiment")}
      subtitle={experiment ? String(experiment.spec.hypothesis || experiment.id) : id}
      actions={experiment ? (
        <>
          <Link href={`/agent-runs/${experiment.workflowRunId}`} style={linkButton}>{isZh ? "查看 Agent 运行" : "View agent run"}</Link>
          <WorkspaceDialog triggerLabel={isZh ? "实验详情" : "Experiment Details"} title={isZh ? "实验上下文" : "Experiment Context"}>
            <ContextStack>
              <ContextGroup title={isZh ? "当前状态" : "Current Status"}>
                <ContextStats>
                  <ContextStat label={isZh ? "状态" : "Status"} value={experiment.status} />
                  <ContextStat label={isZh ? "进度" : "Progress"} value={`${Number(experiment.progress.completed || 0)} / ${Number(experiment.progress.total || trials.length)}`} />
                  <ContextStat label={isZh ? "轮次" : "Rounds"} value={rounds.length} />
                  <ContextStat label="Agent tokens" value={formatInteger(tokenUsage.totalTokens)} />
                </ContextStats>
              </ContextGroup>
              <ContextGroup title={isZh ? "快速入口" : "Quick Links"}>
                <ContextLinks><ContextLink href={`/agent-runs/${experiment.workflowRunId}`}>{isZh ? "查看 Agent 运行" : "View agent run"}</ContextLink><ContextLink href="/research">{isZh ? "返回研究列表" : "Back to research"}</ContextLink></ContextLinks>
              </ContextGroup>
            </ContextStack>
          </WorkspaceDialog>
        </>
      ) : undefined}
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

          {isEffectivenessStudy ? (
            <section style={{ ...panelStyle, marginTop: 18 }}>
              <h2 style={{ marginTop: 0 }}>{isZh ? "预注册有效性判定" : "Pre-registered effectiveness decision"}</h2>
              <div style={summaryGridStyle}>
                <MetricCard label={isZh ? "最终判定" : "Final decision"} value={String(report.decision || (isZh ? "待完成" : "Pending"))} />
                <MetricCard label={isZh ? "当前阶段" : "Current phase"} value={String(experiment.progress.phase || "—")} />
                <MetricCard label={isZh ? "回测预算" : "Backtest budget"} value={`${Number(experiment.progress.scheduled || 0)} / 200`} />
                <MetricCard label={isZh ? "报告状态" : "Report status"} value={String(reportArtifacts.status || "pending")} />
              </div>
              {finalCandidates.map((candidate) => {
                const gates = asRecord(candidate.acceptanceGates);
                return (
                  <div key={String(candidate.paramsHash || candidate.candidateId)} style={{ marginTop: 16 }}>
                    <strong>{String(candidate.rationale || candidate.paramsHash || "Candidate")}</strong>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                      {Object.entries(gates).map(([name, passed]) => (
                        <Badge key={name}>{passed ? "✓" : "✗"} {name}</Badge>
                      ))}
                    </div>
                  </div>
                );
              })}
              {children.length ? (
                <div style={{ marginTop: 18 }}><DenseDataTable columns={childColumns} rows={children} getRowId={(child) => child.id} emptyText={isZh ? "尚无子实验。" : "No child experiments."} ariaLabel={isZh ? "子实验" : "Child experiments"} /></div>
              ) : null}
              {Object.keys(artifactFiles).length ? (
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 18 }}>
                  {([
                    ["json", "JSON"], ["markdownZh", "Markdown 中文"], ["markdownEn", "Markdown EN"],
                    ["pdfZh", "PDF 中文"], ["pdfEn", "PDF EN"],
                  ] as const).map(([kind, label]) => artifactFiles[kind] ? (
                    <a key={kind} href={researchArtifactUrl(experiment.id, kind)} style={linkButton}>{label}</a>
                  ) : null)}
                </div>
              ) : null}
            </section>
          ) : null}

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
            <h2 style={{ marginTop: 0 }}>{isZh ? "自适应轮次与 Pareto 候选" : "Adaptive rounds and Pareto candidates"}</h2>
            {rounds.length ? (
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
                {rounds.map((round) => <Badge key={round.id}>R{round.ordinal} · {round.status}</Badge>)}
              </div>
            ) : <p style={{ color: "#94a3b8" }}>{isZh ? "这是旧版有限网格实验；历史结果仍保持只读。" : "This is a legacy finite-grid experiment; its history remains read-only."}</p>}
            {candidates.length ? (
              <DenseDataTable columns={candidateColumns} rows={candidates} getRowId={(candidate) => candidate.id} emptyText={isZh ? "尚无 Pareto 候选。" : "No Pareto candidates."} ariaLabel={isZh ? "Pareto 候选" : "Pareto candidates"} />
            ) : null}
          </section>

          <section style={{ ...panelStyle, marginTop: 18 }}>
            <h2 style={{ marginTop: 0 }}>{isZh ? "Trial 明细" : "Trial details"}</h2>
            <DenseDataTable columns={trialColumns} rows={trials} getRowId={(trial) => trial.id} emptyText={isZh ? "尚无 Trial。" : "No trials yet."} ariaLabel={isZh ? "Trial 明细" : "Trial details"} />
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
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
    max_rounds_reached: ["达到轮数上限", "Round limit reached"],
    max_trials_reached: ["达到 Backtest 上限", "Backtest limit reached"],
    no_valid_candidates: ["没有有效候选", "No valid candidates"],
    no_novel_candidates: ["没有新候选", "No novel candidates"],
    controller_failed: ["研究控制器失败", "Research controller failed"],
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
const preStyle: CSSProperties = { overflow: "auto", padding: 14, borderRadius: 10, background: "#020617", color: "#bae6fd", fontSize: 12, lineHeight: 1.55 };
const summaryGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 };
const metricCardStyle: CSSProperties = { display: "flex", flexDirection: "column", padding: 14, borderRadius: 12, border: "1px solid rgba(100,116,139,.3)", background: "rgba(15,23,42,.62)" };
