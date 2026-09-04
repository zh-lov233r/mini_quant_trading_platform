import type { CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";

import {
  approveAgentRequest,
  cancelAgentWorkflow,
  getAgentWorkflowRun,
  listAgentApprovals,
  listAgentToolRuns,
  rejectAgentRequest,
  retryAgentWorkflow,
} from "@/api/agentops";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogStack as ContextStack, DialogStat as ContextStat, DialogStats as ContextStats, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { AgentApproval, AgentToolRun, AgentWorkflowRun } from "@/types/agentops";

const TERMINAL = new Set(["completed", "failed", "canceled", "rolled_back"]);

export default function AgentRunPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const runId = typeof router.query.runId === "string" ? router.query.runId : "";
  const [run, setRun] = useState<AgentWorkflowRun | null>(null);
  const [approvals, setApprovals] = useState<AgentApproval[]>([]);
  const [tools, setTools] = useState<AgentToolRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const [nextRun, nextApprovals, nextTools] = await Promise.all([
        getAgentWorkflowRun(runId),
        listAgentApprovals(runId),
        listAgentToolRuns(runId),
      ]);
      setRun(nextRun);
      setApprovals(nextApprovals);
      setTools(nextTools);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (!run || !TERMINAL.has(run.status)) void refresh();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [refresh, run]);

  async function resolveApproval(approval: AgentApproval, approve: boolean) {
    setActing(true);
    try {
      if (approve) await approveAgentRequest(
        approval.id,
        "Approved from Quant Frontend",
        approval.action === "promote_pareto_candidates"
          ? { candidateIds: selectedCandidateIds }
          : {},
      );
      else await rejectAgentRequest(approval.id, "Rejected from Quant Frontend");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActing(false);
    }
  }

  async function cancelRun() {
    if (!run) return;
    setActing(true);
    try {
      await cancelAgentWorkflow(run.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActing(false);
    }
  }

  async function retryRun() {
    if (!run) return;
    setActing(true);
    try {
      const replacement = await retryAgentWorkflow(run);
      await router.push(`/agent-runs/${replacement.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActing(false);
    }
  }

  const pending = approvals.find((item) => item.status === "pending");
  const experimentId = tools.find((item) => item.toolId === "quant.create_category_study")?.externalRef;
  const promotableCandidates = pending?.action === "promote_pareto_candidates"
    ? findPromotableCandidates(pending.payload.review)
    : [];
  const tokenBudget = Number(run?.tokenBudget || 0);
  const totalTokens = Number(run?.totalTokens || 0);
  const tokenPercent = tokenBudget > 0 ? Math.min(100, Math.round(totalTokens / tokenBudget * 100)) : 0;
  const usageSync = run?.resultSummary.researchUsageSync;

  return (
    <AppShell
      title={isZh ? "Agent 运行" : "Agent run"}
      subtitle={run ? `${run.workflowName || "Workflow"} · ${run.id}` : runId}
      actions={(
        <>
          {experimentId ? <Link href={`/research/${experimentId}`} style={linkButton}>{isZh ? "查看实验" : "View experiment"}</Link> : null}
          {run ? (
            <WorkspaceDialog triggerLabel={isZh ? "运行详情" : "Run Details"} title={isZh ? "运行上下文" : "Run Context"}>
              <ContextStack>
                <ContextGroup title={run.workflowName || "Workflow"}><ContextStats><ContextStat label={isZh ? "状态" : "Status"} value={run.status} /><ContextStat label={isZh ? "当前节点" : "Current node"} value={run.currentNodeId || "—"} /><ContextStat label={isZh ? "待审批" : "Pending approval"} value={pending ? (isZh ? "是" : "Yes") : (isZh ? "否" : "No")} /><ContextStat label={isZh ? "工具任务" : "Tool runs"} value={tools.length} /><ContextStat label="Tokens" value={`${totalTokens.toLocaleString()}${tokenBudget ? ` / ${tokenBudget.toLocaleString()}` : ""}`} /></ContextStats></ContextGroup>
                {experimentId ? <ContextGroup title={isZh ? "关联资源" : "Related Resource"}><ContextLinks><ContextLink href={`/research/${experimentId}`}>{isZh ? "查看研究实验" : "View research experiment"}</ContextLink></ContextLinks></ContextGroup> : null}
              </ContextStack>
            </WorkspaceDialog>
          ) : null}
        </>
      )}
    >
      {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}
      {!run ? <p>{isZh ? "加载中…" : "Loading…"}</p> : (
        <>
          <section style={panelStyle}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
              <div><Badge>{run.status}</Badge><span style={{ marginLeft: 12, color: "#94a3b8" }}>{run.currentNodeId || "—"}</span></div>
              {!TERMINAL.has(run.status) ? (
                <button disabled={acting} onClick={() => void cancelRun()} style={dangerButton}>
                  {isZh ? "取消运行" : "Cancel run"}
                </button>
              ) : run.status === "failed" || run.status === "canceled" ? (
                <button disabled={acting} onClick={() => void retryRun()} style={primaryButton}>
                  {isZh ? "重试为新运行" : "Retry as new run"}
                </button>
              ) : null}
            </div>
            {run.lastError ? <p style={{ color: "#fda4af" }}>{run.lastError}</p> : null}
            <div style={{ marginTop: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <strong>{isZh ? "Token 用量" : "Token usage"}</strong>
                <span>{totalTokens.toLocaleString()}{tokenBudget ? ` / ${tokenBudget.toLocaleString()}` : ""}</span>
              </div>
              {tokenBudget ? <div style={progressTrackStyle}><div style={{ ...progressBarStyle, width: `${tokenPercent}%` }} /></div> : null}
              <div style={{ color: "#94a3b8", marginTop: 8, fontSize: 13 }}>
                input {Number(run.inputTokens || 0).toLocaleString()} · cached {Number(run.cachedInputTokens || 0).toLocaleString()} · output {Number(run.outputTokens || 0).toLocaleString()} · reasoning {Number(run.reasoningOutputTokens || 0).toLocaleString()}
              </div>
              {isRecord(usageSync) && usageSync.status === "failed" ? (
                <p style={{ color: "#fbbf24", marginBottom: 0 }}>{isZh ? "最终 token 用量未能同步到 Quant 报告：" : "Final token usage could not be synced to the Quant report: "}{String(usageSync.error || "unknown")}</p>
              ) : null}
            </div>
          </section>

          {pending ? (
            <section style={{ ...panelStyle, marginTop: 18, borderColor: "#d97706" }}>
              <h2 style={{ marginTop: 0 }}>{isZh ? "等待审批" : "Waiting for approval"}</h2>
              <p>{pending.reason}</p>
              <p style={{ color: "#fbbf24" }}>{isZh ? "请核对下方结构化提案、试验数量与校验输出后再批准。" : "Review the proposal, trial count, and validation output below before approving."}</p>
              {pending.payload.review ? <pre style={preStyle}>{JSON.stringify(pending.payload.review, null, 2)}</pre> : null}
              {pending.action === "promote_pareto_candidates" ? (
                <div style={{ display: "grid", gap: 8, marginBottom: 14 }}>
                  <strong>{isZh ? "选择最多 5 个 Pareto rank 1–2 候选；不选也可正常完成" : "Select up to 5 Pareto rank 1–2 candidates; an empty selection also completes"}</strong>
                  {promotableCandidates.map((candidate) => (
                    <label key={candidate.id} style={detailStyle}>
                      <input
                        type="checkbox"
                        checked={selectedCandidateIds.includes(candidate.id)}
                        onChange={(event) => setSelectedCandidateIds((current) => event.target.checked ? [...current, candidate.id].slice(0, 5) : current.filter((id) => id !== candidate.id))}
                      />{" "}R{candidate.paretoRank} · {candidate.paramsHash.slice(0, 8)} · {JSON.stringify(candidate.overrides)}
                    </label>
                  ))}
                </div>
              ) : null}
              <div style={{ display: "flex", gap: 10 }}>
                <button disabled={acting} onClick={() => void resolveApproval(pending, true)} style={primaryButton}>{isZh ? "批准" : "Approve"}</button>
                <button disabled={acting} onClick={() => void resolveApproval(pending, false)} style={dangerButton}>{isZh ? "拒绝" : "Reject"}</button>
              </div>
            </section>
          ) : null}

          <section style={{ ...panelStyle, marginTop: 18 }}>
            <h2 style={{ marginTop: 0 }}>{isZh ? "节点与结构化输出" : "Nodes and structured output"}</h2>
            <div style={{ display: "grid", gap: 12 }}>
              {(run.nodeRuns || []).map((node) => (
                <details key={node.id} open={node.status === "waiting_approval" || node.status === "failed"} style={detailStyle}>
                  <summary style={{ cursor: "pointer" }}><strong>{node.nodeId}</strong> · {node.nodeType} · {node.status}</summary>
                  {node.error ? <p style={{ color: "#fda4af" }}>{node.error}</p> : null}
                  {node.output ? <pre style={preStyle}>{JSON.stringify(node.output, null, 2)}</pre> : null}
                </details>
              ))}
            </div>
          </section>

          {tools.length ? (
            <section style={{ ...panelStyle, marginTop: 18 }}>
              <h2 style={{ marginTop: 0 }}>{isZh ? "外部工具任务" : "External tool tasks"}</h2>
              {tools.map((tool) => (
                <details key={tool.id} style={detailStyle}>
                  <summary><strong>{tool.toolId}</strong> · {tool.status} · {tool.attempts} attempt(s)</summary>
                  {tool.outputSummary ? <p>{tool.outputSummary}</p> : null}
                  {tool.errorMessage ? <p style={{ color: "#fda4af" }}>{tool.errorCode}: {tool.errorMessage}</p> : null}
                  <pre style={preStyle}>{JSON.stringify(tool.response, null, 2)}</pre>
                </details>
              ))}
            </section>
          ) : null}
        </>
      )}
    </AppShell>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

interface PromotableCandidate { id: string; paretoRank: number; paramsHash: string; overrides: Record<string, unknown>; }

function findPromotableCandidates(value: unknown): PromotableCandidate[] {
  const found = new Map<string, PromotableCandidate>();
  function visit(item: unknown) {
    if (Array.isArray(item)) { item.forEach(visit); return; }
    if (!isRecord(item)) return;
    if (typeof item.id === "string" && typeof item.paramsHash === "string" && (item.paretoRank === 1 || item.paretoRank === 2)) {
      found.set(item.id, { id: item.id, paramsHash: item.paramsHash, paretoRank: item.paretoRank, overrides: isRecord(item.overrides) ? item.overrides : {} });
    }
    Object.values(item).forEach(visit);
  }
  visit(value);
  return [...found.values()].sort((left, right) => left.paretoRank - right.paretoRank || left.paramsHash.localeCompare(right.paramsHash));
}

const panelStyle: CSSProperties = { padding: 22, borderRadius: 20, border: "1px solid rgba(100,116,139,.35)", background: "rgba(8,15,24,.8)" };
const detailStyle: CSSProperties = { padding: 14, borderRadius: 12, border: "1px solid rgba(100,116,139,.3)", background: "rgba(15,23,42,.6)" };
const preStyle: CSSProperties = { overflow: "auto", padding: 14, borderRadius: 10, background: "#020617", color: "#bae6fd", fontSize: 12, lineHeight: 1.55 };
const primaryButton: CSSProperties = { padding: "10px 16px", border: 0, borderRadius: 10, background: "#0891b2", color: "white", fontWeight: 800, cursor: "pointer" };
const dangerButton: CSSProperties = { padding: "10px 16px", border: "1px solid #be123c", borderRadius: 10, background: "rgba(159,18,57,.2)", color: "#fecdd3", fontWeight: 800, cursor: "pointer" };
const linkButton: CSSProperties = { padding: "10px 16px", borderRadius: 10, background: "#0891b2", color: "white", textDecoration: "none", fontWeight: 800 };
const progressTrackStyle: CSSProperties = { height: 8, marginTop: 10, overflow: "hidden", borderRadius: 999, background: "#1e293b" };
const progressBarStyle: CSSProperties = { height: "100%", borderRadius: 999, background: "#06b6d4", transition: "width var(--motion-enter) ease" };
