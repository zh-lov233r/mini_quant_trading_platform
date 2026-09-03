import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";

import { getStrategy } from "@/api/strategies";
import AppShell, { PageActionLink } from "@/components/AppShell";
import GuidedStrategyCreate from "@/components/GuidedStrategyCreate";
import { DialogGroup as ContextGroup, DialogLink as ContextLink, DialogLinks as ContextLinks, DialogNote as ContextNote, DialogStack as ContextStack, WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { StrategyOut } from "@/types/strategy";


export default function NewStrategyPage() {
  const router = useRouter();
  const { locale, messages } = useI18n();
  const isZh = locale === "zh-CN";
  const copy = messages.strategyCreate;
  const cloneFrom = Array.isArray(router.query.cloneFrom)
    ? router.query.cloneFrom[0]
    : router.query.cloneFrom;
  const [cloneSource, setCloneSource] = useState<StrategyOut | null>(null);
  const [cloneLoading, setCloneLoading] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);
  const [cloneReloadKey, setCloneReloadKey] = useState(0);

  useEffect(() => {
    if (!router.isReady) return;
    if (!cloneFrom) {
      setCloneSource(null);
      setCloneLoading(false);
      setCloneError(null);
      return;
    }

    let cancelled = false;
    setCloneLoading(true);
    setCloneError(null);
    getStrategy(cloneFrom)
      .then((strategy) => {
        if (!cancelled) setCloneSource(strategy);
      })
      .catch((error: Error) => {
        if (!cancelled) {
          setCloneSource(null);
          setCloneError(error.message || copy.clone.loadFailed);
        }
      })
      .finally(() => {
        if (!cancelled) setCloneLoading(false);
      });
    return () => { cancelled = true; };
  }, [cloneFrom, cloneReloadKey, copy.clone.loadFailed, router.isReady]);

  const cloning = Boolean(cloneFrom);
  return (
    <AppShell
      title={cloning ? copy.clone.pageTitle : copy.page.title}
      subtitle={cloning ? copy.clone.pageSubtitle : copy.page.subtitle}
      actions={
        <>
          <PageActionLink href="/strategies">{copy.page.back}</PageActionLink>
          <WorkspaceDialog triggerLabel={isZh ? "创建指引" : "Creation Guide"} title={isZh ? "创建指引" : "Creation Guide"}>
            <ContextStack>
              <ContextGroup title={isZh ? "安全边界" : "Safety Boundary"}><ContextNote>{isZh ? "新策略首先保存为 Draft。只有通过验证并明确发布为 engine-ready 后，回测引擎才会使用它。" : "New strategies are saved as drafts first. The backtest engine only uses them after validation and explicit engine-ready publication."}</ContextNote></ContextGroup>
              <ContextGroup title={isZh ? "相关入口" : "Related Workspaces"}><ContextLinks><ContextLink href="/research">{isZh ? "打开 Agent 研究" : "Open agent research"}</ContextLink><ContextLink href="/strategies">{isZh ? "查看策略库" : "View strategy library"}</ContextLink></ContextLinks></ContextGroup>
            </ContextStack>
          </WorkspaceDialog>
        </>
      }
    >
      {cloneLoading ? <p>{copy.clone.loading}</p> : null}
      {cloneError ? (
        <section style={{ padding: 20, borderRadius: 18, border: "1px solid rgba(244,63,94,.45)", background: "rgba(127,29,29,.12)", color: "#fecdd3" }}>
          <strong>{copy.clone.loadFailed}</strong>
          <p>{cloneError}</p>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <button type="button" onClick={() => setCloneReloadKey((current) => current + 1)} style={{ padding: "9px 13px", borderRadius: 10, border: "1px solid rgba(244,63,94,.5)", background: "rgba(127,29,29,.28)", color: "#fecdd3", cursor: "pointer" }}>{copy.catalog.retry}</button>
            <Link href="/strategies" style={{ color: "#67e8f9" }}>{copy.page.back}</Link>
          </div>
        </section>
      ) : null}
      {!cloneLoading && !cloneError && (!cloning || cloneSource) ? (
        <GuidedStrategyCreate cloneSource={cloneSource} />
      ) : null}
    </AppShell>
  );
}
