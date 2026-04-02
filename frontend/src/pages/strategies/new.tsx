import Link from "next/link";

import StrategyForm from "@/components/StrategyForm";
import AppShell from "@/components/AppShell";

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
  return (
    <AppShell
      title="创建策略"
      subtitle="在这里定义一套新的策略参数。保存后后端会做标准化校验，并把它沉淀到策略库里，供回测和 paper trading 继续复用。"
      actions={
        <>
          {actionLink("/strategies", "返回策略库")}
          {actionLink("/backtests", "去回测工作台")}
        </>
      }
    >
      <StrategyForm />
    </AppShell>
  );
}
