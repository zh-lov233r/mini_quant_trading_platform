import Link from "next/link";
import type { ReactNode } from "react";

import { useI18n } from "@/i18n/provider";

interface AppShellProps {
  title: string;
  subtitle: string;
  actions?: ReactNode;
  children: ReactNode;
}

export default function AppShell({
  title,
  subtitle,
  actions,
  children,
}: AppShellProps) {
  const { locale, setLocale, messages } = useI18n();
  const navItems = [
    { href: "/dashboard", label: messages.nav.dashboard },
    { href: "/strategies", label: messages.nav.strategies },
    { href: "/stock-baskets", label: messages.nav.stockBaskets },
    { href: "/strategies/new", label: messages.nav.newStrategy },
    { href: "/backtests", label: messages.nav.backtests },
    { href: "/paper-trading", label: messages.nav.paperTrading },
  ];

  return (
    <main
      style={{
        minHeight: "100vh",
        padding: "28px 6px 56px",
        background:
          "radial-gradient(circle at top left, rgba(34,211,238,0.18), transparent 28%), radial-gradient(circle at top right, rgba(245,158,11,0.14), transparent 24%), radial-gradient(circle at bottom left, rgba(59,130,246,0.12), transparent 30%), linear-gradient(180deg, #06131a 0%, #0b1723 45%, #0f172a 100%)",
        color: "#e2e8f0",
        fontFamily:
          "\"Iowan Old Style\", \"Palatino Linotype\", \"Book Antiqua\", Georgia, serif",
      }}
    >
      <div style={{ maxWidth: 1720, margin: "0 auto" }}>
        <header
          style={{
            marginBottom: 20,
            padding: 18,
            borderRadius: 24,
            border: "1px solid rgba(148, 163, 184, 0.16)",
            background: "rgba(8, 15, 24, 0.68)",
            boxShadow: "0 22px 60px rgba(2, 6, 23, 0.42)",
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
                color: "#f8fafc",
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                fontFamily:
                  "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
              }}
            >
              {messages.common.appName}
            </Link>
            <div
              style={{
                display: "flex",
                gap: 10,
                flexWrap: "wrap",
                alignItems: "center",
                justifyContent: "flex-end",
              }}
            >
              <nav style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {navItems.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    style={{
                      padding: "9px 14px",
                      borderRadius: 999,
                      border: "1px solid rgba(148, 163, 184, 0.16)",
                      background: "rgba(15, 23, 42, 0.72)",
                      color: "#dbeafe",
                      textDecoration: "none",
                      fontSize: 14,
                      fontWeight: 600,
                      fontFamily:
                        "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                    }}
                  >
                    {item.label}
                  </Link>
                ))}
              </nav>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: 4,
                  borderRadius: 999,
                  border: "1px solid rgba(148, 163, 184, 0.16)",
                  background: "rgba(15, 23, 42, 0.72)",
                  fontFamily:
                    "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
                }}
              >
                <div
                  aria-label={messages.common.language}
                  title={messages.common.language}
                  style={{
                    width: 28,
                    height: 28,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "rgba(226, 232, 240, 0.72)",
                  }}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="16"
                    height="16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <circle cx="12" cy="12" r="8.5" />
                    <path d="M3.5 12h17" />
                    <path d="M12 3.5a13 13 0 0 1 0 17" />
                    <path d="M12 3.5a13 13 0 0 0 0 17" />
                  </svg>
                </div>
                <button
                  type="button"
                  onClick={() => setLocale("zh-CN")}
                  style={localeButtonStyle(locale === "zh-CN")}
                >
                  {messages.common.chinese}
                </button>
                <button
                  type="button"
                  onClick={() => setLocale("en-US")}
                  style={localeButtonStyle(locale === "en-US")}
                >
                  {messages.common.english}
                </button>
              </div>
            </div>
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
                  fontSize: 46,
                  lineHeight: 1.05,
                  fontWeight: 700,
                  color: "#f8fafc",
                }}
              >
                {title}
              </h1>
              <p
                style={{
                  margin: 0,
                  color: "rgba(226, 232, 240, 0.78)",
                  fontSize: 17,
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

function localeButtonStyle(active: boolean) {
  return {
    padding: "8px 12px",
    borderRadius: 999,
    border: "none",
    background: active ? "rgba(8,145,178,0.92)" : "transparent",
    color: active ? "#f8fafc" : "#cbd5e1",
    fontSize: 13,
    fontWeight: 700,
    cursor: "pointer",
    fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
  };
}
