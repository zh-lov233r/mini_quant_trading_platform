import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { PaginationState } from "@tanstack/react-table";

import { cancelBacktest, deleteBacktest, getBacktestWorkerStatus, listBacktestTasks, retryBacktest } from "@/api/backtests";
import { cancelResearchTrial, getResearchWorkerStatus } from "@/api/research";
import AppShell from "@/components/AppShell";
import BacktestProgressBar from "@/components/BacktestProgressBar";
import Badge from "@/components/Badge";
import { DenseDataTable, type DenseDataColumn } from "@/components/workspace/DenseDataTable";
import { SelectControl } from "@/components/workspace/SelectControl";
import { WorkspaceConfirmDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type {
  BacktestTask,
  BacktestTaskPage,
  BacktestTaskSource,
  BacktestTaskStage,
  BacktestWorkerStatus,
} from "@/types/backtest";
import type { ResearchWorkerStatus } from "@/types/research";
import { backtestTaskStageLabel, isActiveBacktestTask, pageIndexAfterBacktestTaskDelete, shouldPollBacktestTasks, shouldShowBacktestTaskProgress } from "@/utils/backtestTasks";
import { formatBacktestWorkerExecutionModel } from "@/utils/backtestWorkerStatus";
import styles from "@/styles/BacktestTasksPage.module.css";

const EMPTY_PAGE: BacktestTaskPage = { items: [], total: 0, counts: {} };
type DeleteNotice = { tone: "success" | "error"; message: string };

export default function BacktestTasksPage() {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [source, setSource] = useState<BacktestTaskSource | "all">("all");
  const [stage, setStage] = useState<BacktestTaskStage | "active" | "all">("all");
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 25 });
  const [page, setPage] = useState<BacktestTaskPage>(EMPTY_PAGE);
  const [backtestWorker, setBacktestWorker] = useState<BacktestWorkerStatus | null>(null);
  const [researchWorker, setResearchWorker] = useState<ResearchWorkerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingCancel, setPendingCancel] = useState<BacktestTask | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [pendingRetry, setPendingRetry] = useState<BacktestTask | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<BacktestTask | null>(null);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<DeleteNotice | null>(null);

  const refresh = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const [tasks, backtestStatus, researchStatus] = await Promise.all([
        listBacktestTasks({
          source: source === "all" ? undefined : source,
          stage: stage === "all" ? undefined : stage,
          limit: pagination.pageSize,
          offset: pagination.pageIndex * pagination.pageSize,
        }),
        getBacktestWorkerStatus().catch(() => null),
        getResearchWorkerStatus().catch(() => null),
      ]);
      setPage(tasks);
      setBacktestWorker(backtestStatus);
      setResearchWorker(researchStatus);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [pagination.pageIndex, pagination.pageSize, source, stage]);

  useEffect(() => {
    void refresh(true);
  }, [refresh]);

  useEffect(() => {
    if (!shouldPollBacktestTasks(page.items)) return;
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => window.clearInterval(timer);
  }, [page.items, refresh]);

  useEffect(() => {
    if (!deleteNotice) return;
    const timer = window.setTimeout(() => setDeleteNotice(null), 3200);
    return () => window.clearTimeout(timer);
  }, [deleteNotice]);

  const changeSource = (value: string) => {
    setSource(value as BacktestTaskSource | "all");
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  };
  const changeStage = (value: string) => {
    setStage(value as BacktestTaskStage | "active" | "all");
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  };

  const cancelTask = async () => {
    const task = pendingCancel;
    if (!task) return;
    setCancelling(true);
    try {
      if (task.source === "research") {
        if (!task.experiment_id || !task.trial_id) throw new Error("research task identity is incomplete");
        await cancelResearchTrial(task.experiment_id, task.trial_id);
      } else {
        if (!task.run_id) throw new Error("backtest run is unavailable");
        await cancelBacktest(task.run_id);
      }
      setPendingCancel(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCancelling(false);
    }
  };

  const retryTask = async () => {
    const task = pendingRetry;
    if (!task?.run_id) return;
    setRetrying(true);
    try {
      await retryBacktest(task.run_id);
      setPendingRetry(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  };

  const deleteTask = () => {
    const task = pendingDelete;
    if (!task?.deletable || !task.run_id) return;
    const runId = task.run_id;
    const taskLabel = task.strategy_name || task.task_key;
    const nextPageIndex = pageIndexAfterBacktestTaskDelete(pagination.pageIndex, page.items.length);
    setPendingDelete(null);
    setDeletingRunId(runId);
    setDeleteNotice(null);
    void deleteBacktest(runId)
      .then(() => {
        setDeleteNotice({
          tone: "success",
          message: isZh ? `已删除回测任务“${taskLabel}”。` : `Deleted backtest task “${taskLabel}”.`,
        });
        if (nextPageIndex !== pagination.pageIndex) {
          setPagination((current) => ({ ...current, pageIndex: nextPageIndex }));
        } else {
          void refresh();
        }
      })
      .catch((err) => {
        const detail = err instanceof Error ? err.message : String(err);
        setDeleteNotice({
          tone: "error",
          message: isZh ? `删除“${taskLabel}”失败：${detail}` : `Failed to delete “${taskLabel}”: ${detail}`,
        });
      })
      .finally(() => setDeletingRunId(null));
  };

  const columns = useMemo<DenseDataColumn<BacktestTask>[]>(() => [
    { id: "source", header: isZh ? "来源" : "Source", accessor: (item) => item.source, cell: (_value, item) => <Badge tone={item.source === "manual" ? "info" : item.source === "verification" ? "warning" : "neutral"}>{sourceLabel(item.source, isZh)}</Badge>, width: 125 },
    { id: "identity", header: isZh ? "任务" : "Task", accessor: (item) => `${item.strategy_name || ""} ${item.experiment_name || ""}`, cell: (_value, item) => <TaskIdentity task={item} isZh={isZh} />, width: 300 },
    { id: "progress", header: isZh ? "阶段与进度" : "Stage & progress", accessor: (item) => item.stage, cell: (_value, item) => <TaskProgress task={item} isZh={isZh} />, width: 260 },
    { id: "window", header: isZh ? "窗口 / 场景" : "Window / scenario", accessor: (item) => `${item.window_start || ""} ${item.sample_kind || ""} ${item.cost_scenario || ""}`, cell: (_value, item) => <TaskWindow task={item} />, width: 230 },
    { id: "attempt", header: isZh ? "尝试" : "Attempt", accessor: (item) => item.attempt, cell: (_value, item) => `${item.attempt}${item.max_attempts ? ` / ${item.max_attempts}` : ""}`, width: 90 },
    { id: "updated", header: isZh ? "更新时间" : "Updated", accessor: (item) => item.updated_at || "", cell: (_value, item) => formatDateTime(item.updated_at, locale), width: 180 },
    { id: "actions", header: isZh ? "操作" : "Actions", accessor: (item) => item.task_key, cell: (_value, item) => <TaskActions task={item} isZh={isZh} deletingRunId={deletingRunId} onCancel={setPendingCancel} onRetry={setPendingRetry} onDelete={setPendingDelete} />, hideable: false, width: 300 },
  ], [deletingRunId, isZh, locale]);

  return (
    <AppShell
      title={isZh ? "回测任务中心" : "Backtest Task Center"}
      subtitle={isZh ? "统一查看普通回测、研究 Trial 与验证回测的调度和执行进度。" : "Monitor scheduling and execution across manual, research-trial, and verification backtests."}
      actions={<button type="button" className={styles.refreshButton} onClick={() => void refresh(true)}>{isZh ? "刷新" : "Refresh"}</button>}
    >
      <WorkspaceConfirmDialog
        open={pendingCancel !== null}
        onOpenChange={(open) => !open && setPendingCancel(null)}
        title={isZh ? "确认取消任务" : "Cancel task?"}
        description={pendingCancel ? `${sourceLabel(pendingCancel.source, isZh)} · ${pendingCancel.strategy_name || pendingCancel.task_key}` : undefined}
        cancelLabel={isZh ? "返回" : "Back"}
        confirmLabel={cancelling ? (isZh ? "取消中…" : "Cancelling…") : (isZh ? "确认取消" : "Cancel task")}
        confirming={cancelling}
        onConfirm={() => void cancelTask()}
      >
        {pendingCancel?.source === "research"
          ? (isZh ? "只取消这个 Trial；不会自动补跑。证据不完整的候选将不会进入 Pareto 排名，实验会继续执行其他 Trial。" : "Only this trial is cancelled and it will not be replaced. An incomplete candidate is excluded from Pareto ranking while the experiment continues.")
          : (isZh ? "排队任务会立即取消；运行中任务会在当前安全检查点协作停止。" : "Queued work is cancelled immediately. Running work stops cooperatively at the next safe checkpoint.")}
      </WorkspaceConfirmDialog>

      <WorkspaceConfirmDialog
        open={pendingRetry !== null}
        onOpenChange={(open) => !open && setPendingRetry(null)}
        title={isZh ? "确认重试任务" : "Retry task?"}
        description={pendingRetry ? `${sourceLabel(pendingRetry.source, isZh)} · ${pendingRetry.strategy_name || pendingRetry.task_key}` : undefined}
        cancelLabel={isZh ? "返回" : "Back"}
        confirmLabel={retrying ? (isZh ? "正在重试…" : "Retrying…") : (isZh ? "创建重试任务" : "Create retry")}
        confirming={retrying}
        onConfirm={() => void retryTask()}
      >
        {isZh
          ? "系统会用相同执行参数创建一个新的排队任务，并保留当前失败记录用于审计。"
          : "A new queued task will be created with the same execution parameters, while this failed record remains available for audit."}
      </WorkspaceConfirmDialog>

      <WorkspaceConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title={isZh ? "删除回测任务" : "Delete backtest task?"}
        description={pendingDelete ? `${sourceLabel(pendingDelete.source, isZh)} · ${pendingDelete.strategy_name || pendingDelete.task_key}` : undefined}
        cancelLabel={isZh ? "返回" : "Back"}
        confirmLabel={isZh ? "确认删除" : "Delete backtest"}
        confirming={false}
        onConfirm={deleteTask}
      >
        {isZh
          ? "将永久删除所选回测及其交易、信号、净值快照和运行级数据。策略、行情数据、共享支撑/压力数据及其他重试任务会保留。"
          : "This permanently deletes the selected run and its trades, signals, equity snapshots, and run-scoped data. The strategy, market data, shared support/resistance data, and other retry runs are retained."}
      </WorkspaceConfirmDialog>

      {deleteNotice && typeof document !== "undefined" ? createPortal((
        <div className={`${styles.deleteNotice} ${deleteNotice.tone === "error" ? styles.deleteNoticeError : styles.deleteNoticeSuccess}`} role={deleteNotice.tone === "error" ? "alert" : "status"} aria-live={deleteNotice.tone === "error" ? "assertive" : "polite"}>
          <span aria-hidden="true">{deleteNotice.tone === "error" ? "!" : "✓"}</span>
          <span>{deleteNotice.message}</span>
        </div>
      ), document.body) : null}

      <section className={styles.healthGrid} aria-label={isZh ? "执行服务状态" : "Execution service status"}>
        <HealthCard
          title={isZh ? "研究调度" : "Research scheduler"}
          healthy={researchWorker?.state === "idle" || researchWorker?.state === "running"}
          state={researchWorker ? researchWorker.state : "unavailable"}
          detail={researchWorker ? (isZh ? `${researchWorker.active_trials} 个活动 Trial · ${researchWorker.queued_trials} 个等待调度` : `${researchWorker.active_trials} active · ${researchWorker.queued_trials} waiting`) : (isZh ? "状态接口不可用" : "Status unavailable")}
        />
        <HealthCard
          title={isZh ? "回测执行" : "Backtest worker"}
          healthy={backtestWorker?.automation_available === true}
          state={backtestWorker?.manager_state || "unavailable"}
          detail={backtestWorker ? (isZh ? `${backtestWorker.active_jobs} 个执行中 · ${backtestWorker.queued_jobs} 个已入队 · ${formatBacktestWorkerExecutionModel(backtestWorker, true)}` : `${backtestWorker.active_jobs} active · ${backtestWorker.queued_jobs} queued · ${formatBacktestWorkerExecutionModel(backtestWorker, false)}`) : (isZh ? "状态接口不可用" : "Status unavailable")}
        />
      </section>

      <section className={styles.panel}>
        <div className={styles.panelHeader}>
          <div><h2>{isZh ? "全部回测任务" : "All backtest tasks"}</h2><p>{isZh ? `当前筛选共 ${page.total} 条；活动任务优先显示。` : `${page.total} matching tasks; active work is shown first.`}</p></div>
          <div className={styles.filters} role="search" aria-label={isZh ? "筛选回测任务" : "Filter backtest tasks"}>
            <label><span>{isZh ? "来源" : "Source"}</span><SelectControl value={source} onValueChange={changeSource} options={sourceOptions(isZh)} /></label>
            <label><span>{isZh ? "阶段" : "Stage"}</span><SelectControl value={stage} onValueChange={changeStage} options={stageOptions(isZh)} /></label>
          </div>
        </div>

        {error ? <p role="alert" className={styles.error}>{error}</p> : null}
        {loading && page.items.length === 0 ? <p className={styles.empty}>{isZh ? "正在加载任务…" : "Loading tasks…"}</p> : null}
        {!loading || page.items.length > 0 ? (
          <>
            <div className={styles.desktopTable}>
              <DenseDataTable
                columns={columns}
                rows={page.items}
                getRowId={(item) => item.task_key}
                emptyText={isZh ? "没有匹配的回测任务。" : "No matching backtest tasks."}
                ariaLabel={isZh ? "回测任务" : "Backtest tasks"}
                pagination={pagination}
                onPaginationChange={setPagination}
                paginationMode="server"
                rowCount={page.total}
                pageSizeOptions={[25, 50, 100]}
                maxHeight={640}
              />
            </div>
            <div className={styles.mobileCards}>
              {page.items.length ? page.items.map((item) => <TaskCard key={item.task_key} task={item} isZh={isZh} locale={locale} deletingRunId={deletingRunId} onCancel={setPendingCancel} onRetry={setPendingRetry} onDelete={setPendingDelete} />) : <p className={styles.empty}>{isZh ? "没有匹配的回测任务。" : "No matching backtest tasks."}</p>}
              {page.total > pagination.pageSize ? <MobilePager pagination={pagination} total={page.total} setPagination={setPagination} isZh={isZh} /> : null}
            </div>
          </>
        ) : null}
      </section>
    </AppShell>
  );
}

function TaskIdentity({ task, isZh }: { task: BacktestTask; isZh: boolean }) {
  return <div className={styles.identity}><strong>{task.strategy_name || (isZh ? "未知策略" : "Unknown strategy")}</strong>{task.experiment_name ? <span>{task.experiment_name}{task.trial_ordinal != null ? ` · Trial #${task.trial_ordinal}` : ""}</span> : null}</div>;
}

function TaskProgress({ task, isZh }: { task: BacktestTask; isZh: boolean }) {
  if (task.stage === "cancel_requested") {
    return <div className={styles.cancelProgress}><strong>{backtestTaskStageLabel(task.stage, isZh)}</strong>{task.progress ? <BacktestProgressBar progress={task.progress} isZh={isZh} /> : null}</div>;
  }
  if (shouldShowBacktestTaskProgress(task.stage, task.progress != null)) return <BacktestProgressBar progress={task.progress!} isZh={isZh} />;
  return <div className={styles.stageOnly}><strong>{backtestTaskStageLabel(task.stage, isZh)}</strong>{task.stage === "waiting_research" ? <div className={styles.zeroTrack}><span /></div> : null}</div>;
}

function TaskWindow({ task }: { task: BacktestTask }) {
  return <div className={styles.window}><span>{task.window_start || "—"} → {task.window_end || "—"}</span>{task.sample_kind || task.cost_scenario ? <small>{[task.sample_kind, task.cost_scenario].filter(Boolean).join(" · ")}</small> : null}</div>;
}

function TaskActions({ task, isZh, deletingRunId, onCancel, onRetry, onDelete }: { task: BacktestTask; isZh: boolean; deletingRunId: string | null; onCancel: (task: BacktestTask) => void; onRetry: (task: BacktestTask) => void; onDelete: (task: BacktestTask) => void }) {
  return <div className={styles.actions}>{task.run_id ? <Link href={`/backtests/${encodeURIComponent(task.run_id)}`}>{isZh ? "查看" : "View"}</Link> : null}{task.experiment_id ? <Link href={`/research/${encodeURIComponent(task.experiment_id)}`}>{isZh ? "实验" : "Experiment"}</Link> : null}{task.retryable ? <button type="button" className={styles.retryButton} onClick={() => onRetry(task)}>{isZh ? "重试" : "Retry"}</button> : null}{task.cancellable ? <button type="button" onClick={() => onCancel(task)}>{isZh ? "取消" : "Cancel"}</button> : null}{task.deletable ? <button type="button" disabled={deletingRunId === task.run_id} onClick={() => onDelete(task)}>{deletingRunId === task.run_id ? (isZh ? "删除中…" : "Deleting…") : (isZh ? "删除" : "Delete")}</button> : null}</div>;
}

function TaskCard({ task, isZh, locale, deletingRunId, onCancel, onRetry, onDelete }: { task: BacktestTask; isZh: boolean; locale: string; deletingRunId: string | null; onCancel: (task: BacktestTask) => void; onRetry: (task: BacktestTask) => void; onDelete: (task: BacktestTask) => void }) {
  return <article className={styles.taskCard}><div className={styles.cardTop}><Badge>{sourceLabel(task.source, isZh)}</Badge><Badge tone={isActiveBacktestTask(task.stage) ? "info" : task.stage === "failed" ? "warning" : "neutral"}>{backtestTaskStageLabel(task.stage, isZh)}</Badge></div><TaskIdentity task={task} isZh={isZh} /><TaskProgress task={task} isZh={isZh} /><TaskWindow task={task} /><div className={styles.cardFooter}><span>{formatDateTime(task.updated_at, locale)}</span><TaskActions task={task} isZh={isZh} deletingRunId={deletingRunId} onCancel={onCancel} onRetry={onRetry} onDelete={onDelete} /></div></article>;
}

function HealthCard({ title, healthy, state, detail }: { title: string; healthy: boolean; state: string; detail: string }) {
  return <article className={`${styles.healthCard} ${healthy ? styles.healthy : styles.unhealthy}`} role="status"><div><strong>{title}</strong><Badge tone={healthy ? "success" : "warning"}>{state}</Badge></div><p>{detail}</p></article>;
}

function MobilePager({ pagination, total, setPagination, isZh }: { pagination: PaginationState; total: number; setPagination: (next: PaginationState | ((current: PaginationState) => PaginationState)) => void; isZh: boolean }) {
  const pages = Math.max(1, Math.ceil(total / pagination.pageSize));
  return <div className={styles.mobilePager}><button type="button" disabled={pagination.pageIndex === 0} onClick={() => setPagination((current) => ({ ...current, pageIndex: current.pageIndex - 1 }))}>{isZh ? "上一页" : "Previous"}</button><span>{pagination.pageIndex + 1} / {pages}</span><button type="button" disabled={pagination.pageIndex + 1 >= pages} onClick={() => setPagination((current) => ({ ...current, pageIndex: current.pageIndex + 1 }))}>{isZh ? "下一页" : "Next"}</button></div>;
}

function sourceLabel(source: BacktestTaskSource, isZh: boolean): string {
  if (source === "manual") return isZh ? "普通回测" : "Manual";
  if (source === "verification") return isZh ? "验证回测" : "Verification";
  return isZh ? "研究 Trial" : "Research trial";
}

function sourceOptions(isZh: boolean) {
  return [
    { value: "all", label: isZh ? "全部来源" : "All sources" },
    { value: "manual", label: sourceLabel("manual", isZh) },
    { value: "research", label: sourceLabel("research", isZh) },
    { value: "verification", label: sourceLabel("verification", isZh) },
  ];
}

function stageOptions(isZh: boolean) {
  const stages: BacktestTaskStage[] = ["waiting_research", "queued", "preparing", "running", "finalizing", "cancel_requested", "completed", "failed", "cancelled"];
  return [{ value: "all", label: isZh ? "全部阶段" : "All stages" }, { value: "active", label: isZh ? "仅活动任务" : "Active only" }, ...stages.map((value) => ({ value, label: backtestTaskStageLabel(value, isZh) }))];
}

function formatDateTime(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }).format(date);
}
