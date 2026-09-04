import type { ReactNode } from "react";
import Badge from "@/components/Badge";
import { useI18n } from "@/i18n/provider";
import type { BacktestSummaryOut } from "@/types/backtest";
import { formatDateTime, formatDurationMs, formatPercent } from "@/utils/strategy";
import styles from "@/styles/BacktestDetail.module.css";

function formatCurrency(value: number | null | undefined, locale: string): string {
  return typeof value === "number" && !Number.isNaN(value)
    ? value.toLocaleString(locale, { maximumFractionDigits: 2 })
    : "-";
}

export default function BacktestOverview({ run, children }: { run: BacktestSummaryOut; children?: ReactNode }) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  return (
    <section className={`${styles.section} ${styles.overview}`} aria-labelledby="backtest-overview-title">
      <h2 id="backtest-overview-title" className={styles.sectionTitle}>
          {isZh ? "回测概览" : "Backtest Overview"}
      </h2>

      {children}

      <div className={styles.badges}>
        <Badge tone="info">{run.mode}</Badge>
        {run.basket_name ? <Badge>{run.basket_name}</Badge> : null}
        <Badge
          tone={
            run.status === "completed"
              ? "success"
              : run.status === "failed"
                ? "warning"
                : "neutral"
          }
        >
          {run.status}
        </Badge>
        <Badge>v{run.strategy_version}</Badge>
      </div>

      <div className={styles.infoGrid}>
        <div>
          <div className={styles.label}>Run ID</div>
          <div className={styles.value}>{run.id}</div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "策略" : "Strategy"}</div>
          <div className={styles.value}>{run.strategy_name || run.strategy_id}</div>
        </div>
        <div className={styles.windowField}>
          <div className={styles.label}>{isZh ? "区间" : "Window"}</div>
          <div className={`${styles.value} ${styles.windowValue}`}>
            {run.window_start} {"->"} {run.window_end}
          </div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "股票组合" : "Basket"}</div>
          <div className={styles.value}>
            {run.basket_name || (isZh ? "沿用策略原始股票池" : "Use the strategy's original universe")}
          </div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "初始资金" : "Initial Cash"}</div>
          <div className={styles.value}>{formatCurrency(run.initial_cash, locale)}</div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "期末权益" : "Final Equity"}</div>
          <div className={styles.value}>{formatCurrency(run.final_equity, locale)}</div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "完成时间" : "Completed At"}</div>
          <div className={styles.value}>{formatDateTime(run.finished_at || run.requested_at, locale)}</div>
        </div>
        <div>
          <div className={styles.label}>{isZh ? "总耗时" : "Runtime"}</div>
          <div className={styles.value}>{formatDurationMs(run.runtime_ms, locale)}</div>
        </div>
      </div>

      {run.latest_snapshot ? (
        <details className={styles.snapshot}>
          <summary>
            {isZh ? "最新快照" : "Latest Snapshot"}
          </summary>
          <div className={styles.snapshotValues}>
            <div>{isZh ? "时间" : "Time"}: {formatDateTime(run.latest_snapshot.ts || null, locale)}</div>
            <div>{isZh ? "现金" : "Cash"}: {formatCurrency(run.latest_snapshot.cash, locale)}</div>
            <div>{isZh ? "权益" : "Equity"}: {formatCurrency(run.latest_snapshot.equity, locale)}</div>
            <div>{isZh ? "回撤" : "Drawdown"}: {formatPercent(run.latest_snapshot.drawdown ?? null, 2)}</div>
          </div>
        </details>
      ) : null}

      {run.error_message ? (
        <div className={styles.error}>
          {run.error_message}
        </div>
      ) : null}
    </section>
  );
}
