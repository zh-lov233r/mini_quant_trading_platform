import Link from "next/link";

import StrategyForm from "@/components/StrategyForm";
import AppShell from "@/components/AppShell";
import { useI18n } from "@/i18n/provider";

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

export default function NewStrategyPage() {
  const { locale } = useI18n();
  const copy =
    locale === "zh-CN"
      ? {
          title: "创建策略",
          subtitle:
            "在这里定义一套新的策略参数。保存后后端会做标准化校验，并把它沉淀到策略库里，供回测和 paper trading 继续复用",
          back: "返回策略库",
          backtests: "去回测工作台",
        }
      : {
          title: "Create Strategy",
          subtitle:
            "Define a new strategy configuration here. After saving, the backend validates and normalizes it, then stores it in the strategy library for backtesting and paper trading reuse.",
          back: "Back To Strategies",
          backtests: "Open Backtests",
        };
  return (
    <AppShell
      title={copy.title}
      subtitle={copy.subtitle}
      actions={
        <>
          {actionLink("/strategies", copy.back)}
          {actionLink("/backtests", copy.backtests)}
        </>
      }
    >
      <StrategyForm />
    </AppShell>
  );
}
