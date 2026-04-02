import Link from "next/link";

import { useI18n } from "@/i18n/provider";

export default function Home() {
  const { locale } = useI18n();
  const copy = locale === "zh-CN"
    ? {
        title: "量化策略工作台",
        subtitle:
          "在前端定义策略参数，在后端统一存储为可执行配置，再由策略引擎加载 active 策略生成信号。",
        openDashboard: "打开 Dashboard",
        createStrategy: "创建策略",
        viewStrategies: "查看策略库",
        startBacktest: "发起回测",
      }
    : {
        title: "Quant Strategy Workspace",
        subtitle:
          "Define strategy parameters in the frontend, persist executable configs in the backend, and let the strategy engine load active strategies to generate signals.",
        openDashboard: "Open Dashboard",
        createStrategy: "Create Strategy",
        viewStrategies: "View Strategies",
        startBacktest: "Start Backtest",
      };

  return (
    <main
      style={{
        maxWidth: 840,
        margin: "48px auto",
        padding: "0 16px",
        fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif",
        color: "#111827",
      }}
    >
      <section
        style={{
          padding: 28,
          borderRadius: 24,
          background:
            "linear-gradient(140deg, rgba(15,118,110,0.14), rgba(59,130,246,0.10))",
        }}
      >
        <h1 style={{ margin: "0 0 12px", fontSize: 34 }}>{copy.title}</h1>
        <p style={{ margin: "0 0 20px", color: "#4b5563", lineHeight: 1.7 }}>
          {copy.subtitle}
        </p>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <Link
            href="/dashboard"
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              background: "#0f172a",
              color: "#fff",
              textDecoration: "none",
              fontWeight: 600,
            }}
          >
            {copy.openDashboard}
          </Link>
          <Link
            href="/strategies/new"
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              background: "#0f766e",
              color: "#fff",
              textDecoration: "none",
              fontWeight: 600,
            }}
          >
            {copy.createStrategy}
          </Link>
          <Link
            href="/strategies"
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              background: "#fff",
              color: "#0f172a",
              textDecoration: "none",
              fontWeight: 600,
              border: "1px solid #cbd5e1",
            }}
          >
            {copy.viewStrategies}
          </Link>
          <Link
            href="/backtests"
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              background: "#f59e0b",
              color: "#111827",
              textDecoration: "none",
              fontWeight: 600,
            }}
          >
            {copy.startBacktest}
          </Link>
        </div>
      </section>
    </main>
  );
}
