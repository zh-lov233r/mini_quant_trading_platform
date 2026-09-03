import Link from "next/link";
import { DenseDataTable, type DenseDataColumn } from "@/components/workspace/DenseDataTable";
import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import type { DashboardStrategyEvidence } from "@/types/dashboard";
import { getStrategyCategoryPresentation, isStrategyType } from "@/utils/strategy";
import { dashboardNumber } from "./presentation";
import styles from "./Dashboard.module.css";

export function StrategyEvidenceTable({ rows }: { rows: DashboardStrategyEvidence[] }) {
  const { t, locale } = useI18n();
  const columns: DenseDataColumn<DashboardStrategyEvidence>[] = [
    { id: "strategy", header: t("dashboard.strategy"), accessor: r => r.name, width: 200, cell: (_, r) => <div className={styles.cell}><Link href={`/strategies/${r.strategy_id}`}>{r.name}</Link><span>v{r.version}{!r.engine_ready && ` · ${t("dashboard.engineUnavailable")}`}</span></div> },
    { id: "type", header: t("dashboard.type"), accessor: r => r.strategy_type, width: 170, cell: (_, r) => <span>{isStrategyType(r.strategy_type) ? getStrategyCategoryPresentation(r.strategy_type, locale).label : r.strategy_type}<small className={styles.technical}>{r.strategy_type}</small></span> },
    ...(["total_return", "sharpe", "max_drawdown", "trade_count"] as const).map((key, i) => ({
      id: key, header: t(`dashboard.${["return", "sharpe", "drawdown", "trades"][i]}`), accessor: (r: DashboardStrategyEvidence) => r[key], width: 110,
      cell: (_: unknown, r: DashboardStrategyEvidence) => dashboardNumber(r[key], locale, key === "total_return" || key === "max_drawdown"),
    })),
    { id: "window", header: t("dashboard.window"), accessor: r => r.window_start, width: 155, cell: (_, r) => <span>{r.window_start || "—"}<br />{r.window_end || "—"}</span> },
    { id: "evidence", header: t("dashboard.evidenceColumn"), accessor: r => r.evidence_status, sortable: true, width: 200, cell: (_, r) => <div className={styles.cell}><Badge tone={r.evidence_status === "available" ? "info" : "neutral"}>{t(`dashboard.evidenceStatus.${r.evidence_status}`)}</Badge>{r.backtest_id && <Link href={`/backtests/${r.backtest_id}`}>{t("dashboard.open")} ↗</Link>}{r.issues.map(issue => <small key={issue}>{t(`dashboard.issues.${issue}`)}</small>)}</div> },
    { id: "lineage", header: t("dashboard.lineage"), accessor: r => r.verification_status, width: 190, cell: (_, r) => r.experiment_id ? <div className={styles.cell}><Link href={`/research/${r.experiment_id}`}>{t("dashboard.promoted")} ↗</Link><small>{r.verification_status ? t("dashboard.verification", { status: t(`dashboard.statuses.${r.verification_status}`) }) : "—"}</small></div> : t("dashboard.noLineage") },
  ];
  return <div className={styles.table}><DenseDataTable rows={rows} columns={columns} getRowId={r => r.strategy_id} emptyText={t("dashboard.noData")} ariaLabel={t("dashboard.evidence")} /></div>;
}
