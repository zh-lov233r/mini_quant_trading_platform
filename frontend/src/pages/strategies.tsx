import Link from "next/link";
import { useEffect, useState } from "react";

import { listStrategies } from "@/api/strategies";
import type { StrategyOut } from "@/types/strategy";

function getUniverseSummary(strategy: StrategyOut): string {
  const universe = (strategy.params as any)?.universe?.symbols;
  if (!Array.isArray(universe) || universe.length === 0) {
    return "all symbols / runtime selection";
  }
  return universe.join(", ");
}

export default function StrategiesPage() {
  const [items, setItems] = useState<StrategyOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listStrategies()
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || "加载策略失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main
      style={{
        maxWidth: 1100,
        margin: "40px auto",
        padding: "0 16px 48px",
        fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif",
        color: "#111827",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 16,
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ marginBottom: 8 }}>策略库</h1>
          <p style={{ margin: 0, color: "#6b7280" }}>
            查看所有已存储策略，确认哪些策略已经可以直接被引擎消费。
          </p>
        </div>
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
          新建策略
        </Link>
      </div>

      {loading && <p>加载中...</p>}
      {error && <p style={{ color: "crimson" }}>{error}</p>}

      {!loading && !error && items.length === 0 && (
        <div
          style={{
            padding: 24,
            borderRadius: 16,
            border: "1px solid #e5e7eb",
            background: "#fff",
          }}
        >
          暂无策略，先去创建一个吧。
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: 16,
        }}
      >
        {items.map((item) => (
          <article
            key={item.id}
            style={{
              padding: 20,
              borderRadius: 18,
              border: "1px solid #e5e7eb",
              background: "#fff",
              boxShadow: "0 8px 30px rgba(15, 23, 42, 0.06)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                alignItems: "flex-start",
              }}
            >
              <div>
                <h2 style={{ margin: "0 0 8px", fontSize: 18 }}>{item.name}</h2>
                <div style={{ color: "#6b7280", fontSize: 14 }}>
                  {item.strategy_type} v{item.version}
                </div>
              </div>
              <span
                style={{
                  padding: "4px 10px",
                  borderRadius: 999,
                  fontSize: 12,
                  fontWeight: 700,
                  background: item.engine_ready ? "#dcfce7" : "#fef3c7",
                  color: item.engine_ready ? "#166534" : "#92400e",
                }}
              >
                {item.engine_ready ? "engine-ready" : "stored-only"}
              </span>
            </div>

            <p style={{ minHeight: 48, color: "#374151", lineHeight: 1.6 }}>
              {item.description || "暂无说明"}
            </p>

            <div style={{ fontSize: 14, color: "#4b5563", lineHeight: 1.8 }}>
              <div>状态: {item.status}</div>
              <div>股票池: {getUniverseSummary(item)}</div>
              <div>创建时间: {item.created_at || "-"}</div>
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}
