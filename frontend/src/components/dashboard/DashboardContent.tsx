import Link from "next/link";
import { useState } from "react";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { KpiStrip, DensePanel } from "@/components/workspace/WorkspacePrimitives";
import { WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { DashboardOverview } from "@/types/dashboard";
import { formatDateTime, getStrategyCategoryPresentation, isStrategyType } from "@/utils/strategy";
import { dashboardNumber, healthPresentation, progressLinks } from "./presentation";
import { StrategyEvidenceTable } from "./StrategyEvidenceTable";
import styles from "./Dashboard.module.css";

export function DashboardSkeleton() {
  const { t } = useI18n();
  return <div className={styles.skeleton} role="status" aria-label={t("dashboard.loading")}>
    <p>{t("dashboard.loading")}</p><div /><div /><div />
  </div>;
}

export function DashboardContent({ data }: { data: DashboardOverview }) {
  const { t, locale } = useI18n();
  const [allAlerts, setAllAlerts] = useState(false);
  return <div className={styles.content}>
    <section className={styles.system} aria-label={t("dashboard.system")}>
      <div className={styles.chips}>{data.system.map(item => <div key={item.key} title={t(`dashboard.reasons.${item.reason}`)}>
        <span>{t(`dashboard.keys.${item.key}`)}</span>{" "}
        <Badge tone={healthPresentation[item.status].tone}><span aria-hidden="true">{healthPresentation[item.status].icon}&nbsp;</span>{t(`dashboard.health.${item.status}`)}</Badge>
      </div>)}</div>
      <WorkspaceDialog triggerLabel={t("dashboard.systemDetails")} title={t("dashboard.systemDetails")}>
        <div className={styles.details}>{data.system.map(item => <section key={item.key}>
          <h3>{t(`dashboard.keys.${item.key}`)} · {t(`dashboard.health.${item.status}`)}</h3>
          <p>{t(`dashboard.reasons.${item.reason}`)}</p>
          {item.capacity !== null && <p>{t("dashboard.capacity")}: {item.active} / {item.capacity} · {t("dashboard.queued")}: {item.queued}</p>}
          {item.submit_orders !== null && <p>{t("dashboard.submit")}: {t(item.submit_orders ? "dashboard.yes" : "dashboard.no")}</p>}
          {item.last_run_status && <p>{t("dashboard.lastRun")}: {t(`dashboard.statuses.${item.last_run_status}`)}</p>}
          {item.observed_at && <p>{t("dashboard.observed")}: {formatDateTime(item.observed_at, locale)}</p>}
        </section>)}</div>
      </WorkspaceDialog>
    </section>
    <KpiStrip className={styles.kpis}>{Object.entries(data.research_kpis).map(([key, value]) => <MetricCard key={key}
      accent="#5eead4" label={t(`dashboard.kpis.${key}`)} value={dashboardNumber(value, locale)} hint={t(`dashboard.kpiHints.${key}`)} />)}</KpiStrip>
    <div className={styles.grid}>
      <DensePanel>
        <h2>{t("dashboard.actions")} <span className={styles.count}>{data.alerts.length}</span></h2>
        {data.alerts.length === 0 ? <div className={styles.empty}><strong>{t("dashboard.noAlerts")}</strong><p>{t("dashboard.noAlertsHint")}</p></div> : <ul className={styles.alerts}>
          {(allAlerts ? data.alerts : data.alerts.slice(0, 5)).map(alert => <li key={alert.id} className={styles[alert.severity]}>
            <div className={styles.alertHeading}><strong><span aria-hidden="true">{alert.severity === "critical" ? "! " : alert.severity === "warning" ? "▲ " : "○ "}</span>{t(`dashboard.severity.${alert.severity}`)}</strong><span>{t("dashboard.count", { count: alert.count })}</span></div>
            <p>{t(`dashboard.alerts.${alert.code}`)}</p>
            <div className={styles.row}><span>{alert.occurred_at ? formatDateTime(alert.occurred_at, locale) : "—"}</span><Link href={alert.href}>{t("dashboard.handle")} →</Link></div>
          </li>)}
        </ul>}
        {data.alerts.length > 5 && <button className={styles.button} aria-expanded={allAlerts} onClick={() => setAllAlerts(value => !value)}>{t(allAlerts ? "dashboard.less" : "dashboard.all")}</button>}
      </DensePanel>
      <DensePanel>
        <h2>{t("dashboard.progress")}</h2><p className={styles.hint}>{t("dashboard.progressHint")}</p>
        <div className={styles.progress}>{(Object.keys(progressLinks) as Array<keyof typeof progressLinks>).map(key => <Link href={progressLinks[key]} key={key}>
          <span>{t(`dashboard.stages.${key}`)}</span><strong>{dashboardNumber(data.research_progress[key], locale)}</strong><span aria-hidden="true">↗</span>
        </Link>)}</div>
        <p className={styles.hint}>{t("dashboard.waiting", { count: data.task_summary.waiting_research })}</p>
        <p className={styles.hint}>{t("dashboard.completed", { count: data.task_summary.completed_last_24h })}</p>
        <Link href="/backtest-tasks" className={styles.actionLink}>{t("dashboard.tasks")} →</Link>
      </DensePanel>
    </div>
    <DensePanel><div className={styles.sectionHeading}><h2>{t("dashboard.evidence")}</h2><Link href="/strategies">{t("dashboard.all")} →</Link></div>
      <p className={styles.hint}>{t("dashboard.evidenceHint")}</p>
      {data.strategy_evidence.length ? <StrategyEvidenceTable rows={data.strategy_evidence} /> : <div className={styles.empty}><p>{t("dashboard.noStrategies")}</p><Link href="/strategies/new">{t("dashboard.create")} →</Link></div>}
    </DensePanel>
    <div className={styles.grid}>
      <DensePanel>
        <div className={styles.sectionHeading}><h2>{t("dashboard.paper")}</h2><Link href="/paper-trading">{t("dashboard.all")} →</Link></div>
        <p className={styles.hint}>{t("dashboard.paperHint")}</p><p className={styles.hint}>{t("dashboard.paperCounts", { accounts: data.paper_summary.account_count, portfolios: data.paper_summary.portfolio_count })}</p>
        {data.paper_summary.portfolios.length === 0 && <div className={styles.empty}><p>{t("dashboard.noPaper")}</p><Link href="/paper-trading">{t("dashboard.paperLink")} →</Link></div>}
        <div className={styles.paperList}>{data.paper_summary.portfolios.map(p => <article key={p.portfolio_id}>
          <Link className={styles.paperTitle} href={`/paper-trading/portfolios/${p.portfolio_id}`}>{p.name} ↗</Link><p className={styles.hint}>{p.account_name}</p>
          <dl><div><dt>{t("dashboard.allocation")}</dt><dd>{p.allocation_count}</dd></div><div><dt>{t("dashboard.allocationTotal")}</dt><dd>{dashboardNumber(p.allocation_total, locale, true)}</dd></div><div><dt>{t("dashboard.eligible")}</dt><dd>{t(p.auto_run_eligible ? "dashboard.yes" : "dashboard.no")}</dd></div></dl>
          <div className={styles.run}><strong>{t("dashboard.recentStrategy")}</strong><span>{p.latest_strategy_name || t("dashboard.neverRun")}</span>
            {p.latest_run_status && <Badge tone={p.latest_run_status === "failed" ? "warning" : "neutral"}>{t(`dashboard.statuses.${p.latest_run_status}`)}</Badge>}
            {p.submit_orders !== null && <Badge>{t(p.submit_orders ? "dashboard.submittedMode" : "dashboard.dryRun")}</Badge>}
          </div><p className={styles.hint}>{p.latest_run_at ? formatDateTime(p.latest_run_at, locale) : "—"}</p>
        </article>)}</div>
      </DensePanel>
      <DensePanel><h2>{t("dashboard.activity")}</h2><p className={styles.hint}>{t("dashboard.activityHint")}</p>
        {data.activity.length === 0 ? <div className={styles.empty}><p>{t("dashboard.noActivity")}</p><Link href="/research">{t("dashboard.progress")} →</Link></div> : <ul className={styles.activity}>{data.activity.slice(0, 10).map(item => {
          const category = item.strategy_type && isStrategyType(item.strategy_type) ? getStrategyCategoryPresentation(item.strategy_type, locale) : null;
          return <li key={item.id} style={category ? { borderLeft: `3px solid ${category.accent}`, background: `rgba(${category.accentRgb}, .1)`, padding: 12 } : undefined}>
          {category ? <span style={{ color: category.accent, fontSize: 12 }}>{category.label}</span> : null}
          <div className={styles.row}><Badge tone={item.status === "failed" || item.status === "partially_failed" ? "warning" : "neutral"}>{t(`dashboard.categories.${item.category}`)} · {t(`dashboard.statuses.${item.status}`)}</Badge></div>
          <Link href={item.href}>{item.name}</Link><time dateTime={item.occurred_at}>{formatDateTime(item.occurred_at, locale)}</time>
        </li>; })}</ul>}
      </DensePanel>
    </div>
  </div>;
}
