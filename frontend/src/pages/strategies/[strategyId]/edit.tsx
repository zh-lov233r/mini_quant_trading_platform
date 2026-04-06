import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

import { getStrategy } from "@/api/strategies";
import AppShell from "@/components/AppShell";
import StrategyForm from "@/components/StrategyForm";
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
          ? "直接修改策略库中这条策略的参数、说明和状态。保存后，回测和后续执行会读取这份更新后的配置。"
          : "Update the persisted parameters, description, and status for this strategy. Once saved, backtests and future execution reads will use the refreshed configuration."
      }
      actions={
        <>
          {actionLink(
            strategy ? `/strategies/${encodeURIComponent(strategy.id)}` : "/strategies",
            isZh ? "返回详情" : "Back To Detail"
          )}
          {actionLink("/strategies", isZh ? "返回策略库" : "Back To Strategies")}
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
