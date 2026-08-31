import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

import { getStrategy } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import StrategyForm from "@/components/StrategyForm";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogNote as ContextNote, DialogStack as ContextStack, DialogStat as ContextStat, DialogStats as ContextStats, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { StrategyOut } from "@/types/strategy";

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

export default function EditStrategyPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const strategyId = Array.isArray(router.query.strategyId)
    ? router.query.strategyId[0]
    : router.query.strategyId;

  const [strategy, setStrategy] = useState<StrategyOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!router.isReady || !strategyId) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    getStrategy(strategyId)
      .then((item) => {
        if (!cancelled) {
          setStrategy(item);
          setLoading(false);
        }
      })
      .catch((err: any) => {
        if (!cancelled) {
          setStrategy(null);
          setError(err?.message || (isZh ? "加载策略失败" : "Failed to load strategy"));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isZh, router.isReady, strategyId]);

  return (
    <AppShell
      title={isZh ? "编辑策略参数" : "Edit Strategy Parameters"}
      subtitle={
        isZh
          ? "直接修改策这条策略的参数、说明和状态。保存后，回测和后续执行会读取这份更新后的配置"
          : "Update the persisted parameters, description, and status for this strategy. Once saved, backtests and future execution reads will use the refreshed configuration"
      }
      actions={
        <>
          {actionLink(
            strategy ? `/strategies/${encodeURIComponent(strategy.id)}` : "/strategies",
            isZh ? "返回详情" : "Back To Detail"
          )}
          {actionLink("/strategies", isZh ? "返回策略库" : "Back To Strategies")}
          {strategy ? (
            <WorkspaceDialog triggerLabel={isZh ? "策略摘要" : "Strategy Summary"} title={isZh ? "策略上下文" : "Strategy Context"}>
              <ContextStack>
                <ContextGroup title={strategy.name}><ContextStats><ContextStat label={isZh ? "技术类型" : "Technical type"} value={strategy.strategy_type} /><ContextStat label={isZh ? "版本" : "Version"} value={`v${strategy.version}`} /><ContextStat label={isZh ? "状态" : "Status"} value={strategy.status} /><ContextStat label="Engine ready" value={strategy.engine_ready ? (isZh ? "是" : "Yes") : (isZh ? "否" : "No")} /></ContextStats></ContextGroup>
                <ContextGroup title={isZh ? "保存影响" : "Save Impact"}><ContextNote>{isZh ? "保存后，新回测和后续执行会读取更新后的配置；既有历史回测保持不变。" : "New backtests and future execution use the updated configuration; existing historical runs remain unchanged."}</ContextNote></ContextGroup>
                <ContextGroup title={isZh ? "快速入口" : "Quick Links"}><ContextLinks><ContextLink href={`/strategies/${encodeURIComponent(strategy.id)}`}>{isZh ? "策略详情" : "Strategy detail"}</ContextLink><ContextLink href="/backtests">{isZh ? "回测工作台" : "Backtest workspace"}</ContextLink></ContextLinks></ContextGroup>
              </ContextStack>
            </WorkspaceDialog>
          ) : null}
        </>
      }
    >
      {loading ? <p>{isZh ? "加载中..." : "Loading..."}</p> : null}
      {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}
      {!loading && !error && strategy ? (
        <StrategyForm mode="edit" initialStrategy={strategy} />
      ) : null}
    </AppShell>
  );
}
