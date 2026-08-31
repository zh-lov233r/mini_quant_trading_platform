import Link from "next/link";

import AppShell from "@/components/AppShell";
import GuidedStrategyCreate from "@/components/GuidedStrategyCreate";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogNote as ContextNote, DialogStack as ContextStack, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
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
  const { locale, messages } = useI18n();
  const isZh = locale === "zh-CN";
  const copy = messages.strategyCreate;
  return (
    <AppShell
      title={copy.page.title}
      subtitle={copy.page.subtitle}
      actions={
        <>
          {actionLink("/strategies", copy.page.back)}
          <WorkspaceDialog triggerLabel={isZh ? "创建指引" : "Creation Guide"} title={isZh ? "创建指引" : "Creation Guide"}>
            <ContextStack>
              <ContextGroup title={isZh ? "安全边界" : "Safety Boundary"}><ContextNote>{isZh ? "新策略首先保存为 Draft。只有通过验证并明确发布为 engine-ready 后，回测引擎才会使用它。" : "New strategies are saved as drafts first. The backtest engine only uses them after validation and explicit engine-ready publication."}</ContextNote></ContextGroup>
              <ContextGroup title={isZh ? "相关入口" : "Related Workspaces"}><ContextLinks><ContextLink href="/research">{isZh ? "打开 Agent 研究" : "Open agent research"}</ContextLink><ContextLink href="/strategies">{isZh ? "查看策略库" : "View strategy library"}</ContextLink></ContextLinks></ContextGroup>
            </ContextStack>
          </WorkspaceDialog>
        </>
      }
    >
      <GuidedStrategyCreate />
    </AppShell>
  );
}
