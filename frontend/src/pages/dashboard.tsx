import AppShell, { PageActionLink } from "@/components/AppShell";
import { DashboardContent, DashboardSkeleton } from "@/components/dashboard/DashboardContent";
import { useDashboardOverview } from "@/hooks/useDashboardOverview";
import { useI18n } from "@/i18n/provider";
import { formatDateTime } from "@/utils/strategy";
import styles from "@/components/dashboard/Dashboard.module.css";

export default function DashboardPage() {
  const { t, locale } = useI18n();
  const { data, failed, refreshing, refresh } = useDashboardOverview();
  return <AppShell title={t("dashboard.title")} actions={<div className={styles.pageActions}>
    <PageActionLink href="/strategies/new" primary>{t("dashboard.create")}</PageActionLink>
    <PageActionLink href="/backtests">{t("dashboard.backtest")}</PageActionLink>
    <PageActionLink href="/backtest-tasks">{t("dashboard.tasks")}</PageActionLink>
    <PageActionLink href="/paper-trading">{t("dashboard.paperLink")}</PageActionLink>
  </div>}>
    <div className={styles.toolbar}><div><p>{t("dashboard.subtitle")}</p><p className={styles.hint}>{data ? t("dashboard.refreshed", { time: formatDateTime(data.generated_at, locale) }) : t("dashboard.refreshHint")}</p></div>
      <button className={styles.button} disabled={refreshing} onClick={() => void refresh()}>{t(refreshing ? "dashboard.refreshing" : "dashboard.refresh")}</button>
    </div>
    {failed && <div className={styles.error} role="alert"><strong>{t("dashboard.failure")}</strong>{data && <p>{t("dashboard.stale")}</p>}<button className={styles.button} disabled={refreshing} onClick={() => void refresh()}>{t("dashboard.retry")}</button></div>}
    {!data && refreshing && <DashboardSkeleton />}
    {data && <DashboardContent data={data} />}
  </AppShell>;
}
