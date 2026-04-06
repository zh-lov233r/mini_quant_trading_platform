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
        color: "#e2e8f0",
      }}
    >
      <section
        style={{
          padding: 28,
          borderRadius: 24,
          background:
            "radial-gradient(circle at top right, rgba(45,212,191,0.14), transparent 30%), linear-gradient(140deg, rgba(8,15,24,0.94), rgba(15,23,42,0.9))",
          border: "1px solid rgba(71, 85, 105, 0.32)",
          boxShadow: "0 24px 56px rgba(2, 6, 23, 0.42)",
        }}
      >
        <h1 style={{ margin: "0 0 12px", fontSize: 34 }}>{copy.title}</h1>
        <p style={{ margin: "0 0 20px", color: "rgba(203, 213, 225, 0.82)", lineHeight: 1.7 }}>
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
              background: "rgba(15, 23, 42, 0.82)",
              color: "#e2e8f0",
              textDecoration: "none",
              fontWeight: 600,
              border: "1px solid rgba(71, 85, 105, 0.34)",
            }}
          >
            {copy.viewStrategies}
          </Link>
          <Link
            href="/backtests"
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              background: "#d97706",
              color: "#fff7ed",
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
