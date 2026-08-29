import Link from "next/link";

import AppShell from "@/components/AppShell";
import GuidedStrategyCreate from "@/components/GuidedStrategyCreate";
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
  const { messages } = useI18n();
  const copy = messages.strategyCreate;
  return (
    <AppShell
      title={copy.page.title}
      subtitle={copy.page.subtitle}
      actions={
        actionLink("/strategies", copy.page.back)
      }
    >
      <GuidedStrategyCreate />
    </AppShell>
  );
}
