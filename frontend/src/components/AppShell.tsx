import Link from "next/link";
import type { ReactNode } from "react";

interface AppShellProps {
  title: string;
  subtitle: string;
  actions?: ReactNode;
  children: ReactNode;
}

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/strategies", label: "策略库" },
  { href: "/stock-baskets", label: "股票库" },
  { href: "/strategies/new", label: "创建策略" },
  { href: "/backtests", label: "回测" },
  { href: "/market", label: "行情" },
  { href: "/orders", label: "订单" },
];

export default function AppShell({
  title,
  subtitle,
  actions,
  children,
}: AppShellProps) {
  return (
    <main
      style={{
        minHeight: "100vh",
        padding: "28px 16px 56px",
        background:
          "radial-gradient(circle at top left, rgba(255,244,214,0.8), transparent 34%), linear-gradient(180deg, #f7f4ee 0%, #fbfaf7 45%, #f4f6f7 100%)",
        color: "#111827",
        fontFamily:
          "\"Iowan Old Style\", \"Palatino Linotype\", \"Book Antiqua\", Georgia, serif",
      }}
    >
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <header
          style={{
            marginBottom: 20,
            padding: 18,
            borderRadius: 24,
            border: "1px solid rgba(15, 23, 42, 0.08)",
            background: "rgba(255,255,255,0.68)",
            boxShadow: "0 18px 50px rgba(15, 23, 42, 0.06)",
            backdropFilter: "blur(8px)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 16,
              flexWrap: "wrap",
              marginBottom: 14,
            }}
          >
            <Link
              href="/"
              style={{
                textDecoration: "none",
                color: "#0f172a",
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                fontFamily:
                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              Quant Strategy Workspace
            </Link>
            <nav style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  style={{
                    padding: "8px 12px",
                    borderRadius: 999,
                    border: "1px solid rgba(148, 163, 184, 0.28)",
                    background: "rgba(255,255,255,0.72)",
                    color: "#1f2937",
                    textDecoration: "none",
                    fontSize: 13,
                    fontWeight: 600,
                    fontFamily:
                      "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                  }}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-end",
              gap: 18,
              flexWrap: "wrap",
            }}
          >
            <div style={{ maxWidth: 760 }}>
              <h1
                style={{
                  margin: "0 0 10px",
                  fontSize: 42,
                  lineHeight: 1.05,
                  fontWeight: 700,
                }}
              >
                {title}
              </h1>
              <p
                style={{
                  margin: 0,
                  color: "#475569",
                  fontSize: 16,
                  lineHeight: 1.7,
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
              >
                {subtitle}
              </p>
            </div>

            {actions ? (
              <div
                style={{
                  display: "flex",
                  gap: 12,
                  flexWrap: "wrap",
                  alignItems: "center",
                }}
              >
                {actions}
              </div>
            ) : null}
          </div>
        </header>

        {children}
      </div>
    </main>
  );
}
