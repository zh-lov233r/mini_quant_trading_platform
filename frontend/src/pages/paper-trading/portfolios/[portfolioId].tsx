import type { CSSProperties, ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getPaperAccountWorkspace,
  listStrategyPortfolios,
} from "@/api/paper-accounts";
import AppShell from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { useI18n } from "@/i18n/provider";
import type {
  PaperTradingWorkspaceOut,
  StrategyPortfolioOut,
  StrategyPortfolioWorkspaceOut,
} from "@/types/paper-account";
import { formatDateTime, formatPercent } from "@/utils/strategy";

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

function sectionCard(title: string, subtitle: string, children: ReactNode) {
  return (
    <section style={sectionCardStyle}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={sectionTitleStyle}>{title}</h2>
        <p style={sectionSubtitleStyle}>{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

function infoRow(label: string, value: ReactNode) {
  return (
    <div style={infoRowStyle}>
      <div style={infoLabelStyle}>{label}</div>
      <div style={infoValueStyle}>{value}</div>
    </div>
  );
}

function formatMoney(value?: number | null, locale = "en-US", currency = "USD"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatNumber(value?: number | null, locale = "en-US", digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  return value.toLocaleString(locale, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export default function PortfolioDetailPage() {
  const router = useRouter();
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const txt = useCallback(
    (zh: string, en: string) => (isZh ? zh : en),
    [isZh]
  );

  const portfolioId = Array.isArray(router.query.portfolioId)
    ? router.query.portfolioId[0]
    : router.query.portfolioId;
  const accountIdFromQuery = Array.isArray(router.query.accountId)
    ? router.query.accountId[0]
    : router.query.accountId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<PaperTradingWorkspaceOut | null>(null);
  const [portfolioMeta, setPortfolioMeta] = useState<StrategyPortfolioOut | null>(null);

  useEffect(() => {
    if (!router.isReady || !portfolioId) {
      return;
    }

    let cancelled = false;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);

        let matchedPortfolio: StrategyPortfolioOut | null = null;
        if (accountIdFromQuery) {
          const scopedPortfolios = await listStrategyPortfolios(accountIdFromQuery);
          matchedPortfolio =
            scopedPortfolios.find((item) => item.id === portfolioId) || null;
        }

        if (!matchedPortfolio) {
          const allPortfolios = await listStrategyPortfolios();
          matchedPortfolio =
            allPortfolios.find((item) => item.id === portfolioId) || null;
        }

        if (!matchedPortfolio) {
          throw new Error(
            txt("没有找到这个 portfolio。它可能已经被删除。", "This portfolio was not found. It may have been deleted.")
          );
        }

        const workspacePayload = await getPaperAccountWorkspace(
          matchedPortfolio.paper_account_id
        );

        if (cancelled) {
          return;
        }

        setPortfolioMeta(matchedPortfolio);
        setWorkspace(workspacePayload);
        setLoading(false);
      } catch (err: any) {
        if (!cancelled) {
          setError(
            err?.message ||
              txt("加载 portfolio 详情失败。", "Failed to load portfolio details.")
          );
          setLoading(false);
        }
      }
    };

    void load();

    return () => {
      cancelled = true;
    };
  }, [accountIdFromQuery, portfolioId, router.isReady, txt]);

  const portfolio = useMemo(() => {
    if (!workspace || !portfolioMeta) {
      return null;
    }
    return (
      workspace.portfolios.find((item) => item.id === portfolioMeta.id) ||
      workspace.portfolios.find((item) => item.name === portfolioMeta.name) ||
      null
    );
  }, [portfolioMeta, workspace]);

  const brokerCurrency = workspace?.broker_account?.currency || "USD";

  const portfolioTransactions = useMemo(() => {
    if (!workspace || !portfolio) {
      return [];
    }
    return workspace.recent_transactions.filter(
      (item) => item.portfolio_name === portfolio.name
    );
  }, [portfolio, workspace]);

  const autoRunEnabledCount = useMemo(
    () =>
      portfolio?.strategies.reduce(
        (count, item) => count + (item.auto_run_enabled ? 1 : 0),
        0
      ) ?? 0,
    [portfolio]
  );

  const activeStrategyCount = useMemo(
    () =>
      portfolio?.strategies.filter((item) => item.allocation_status === "active")
        .length ?? 0,
    [portfolio]
  );

  return (
    <AppShell
      title={portfolio?.name || txt("Portfolio 详情", "Portfolio Detail")}
      subtitle={
        portfolio?.description ||
        txt(
          "查看这个 portfolio 的策略配置、最新运行结果，以及本地交易记录。",
          "Review this portfolio's strategy setup, latest execution result, and local trade records."
        )
      }
      actions={
        <>
          {actionLink("/paper-trading#portfolios", txt("返回工作台", "Back To Workspace"))}
          <button
            type="button"
            onClick={() => router.reload()}
            style={headerButtonStyle}
          >
            {txt("刷新", "Refresh")}
          </button>
        </>
      }
    >
      {loading ? (
        <div style={sectionCardStyle}>
          {txt("正在加载 portfolio 详情...", "Loading portfolio details...")}
        </div>
      ) : null}

      {error ? <div style={errorPanelStyle}>{error}</div> : null}

      {!loading && !error && !portfolio ? (
        <div style={errorPanelStyle}>
          {txt(
            "这个 portfolio 目前没有出现在所属账户的工作台数据里。",
            "This portfolio is not currently present in its account workspace payload."
          )}
        </div>
      ) : null}

      {!loading && !error && portfolio ? (
        <div style={{ display: "grid", gap: 18 }}>
          <div style={metricGridStyle}>
            <MetricCard
              label={txt("所属账户", "Owning Account")}
              value={workspace?.account.name || "-"}
              hint={txt(
                "这个 portfolio 挂在哪个 paper trading account 下。",
                "Which paper trading account owns this portfolio."
              )}
            />
            <MetricCard
              label={txt("状态", "Status")}
              value={portfolio.status}
              hint={txt("portfolio 当前状态。", "Current portfolio status.")}
            />
            <MetricCard
              label={txt("Active 策略数", "Active Strategies")}
              value={String(activeStrategyCount)}
              hint={txt(
                "allocation 状态为 active 的策略数量。",
                "Number of strategies whose allocation status is active."
              )}
            />
            <MetricCard
              label={txt("日调度开启", "Daily Auto-Run On")}
              value={String(autoRunEnabledCount)}
              hint={txt(
                "开启后才会参与每日 scheduler 自动运行。",
                "Only these strategies participate in the daily scheduler."
              )}
            />
            <MetricCard
              label={txt("总配比", "Total Allocation")}
              value={formatPercent(portfolio.active_allocation_pct_total, 0)}
              hint={txt(
                "当前 active allocation 的配比合计。",
                "Combined allocation percentage of active strategies."
              )}
            />
            <MetricCard
              label={txt("最新虚拟权益", "Latest Virtual Equity")}
              value={formatMoney(portfolio.latest_run_equity, locale, brokerCurrency)}
              hint={txt(
                "最近一次 portfolio 相关策略运行后记录的权益。",
                "Equity recorded after the latest portfolio-related strategy run."
              )}
            />
            <MetricCard
              label={txt("最新收益率", "Latest Return")}
              value={formatPercent(portfolio.latest_run_return_pct ?? null, 2)}
              hint={txt(
                "基于最近一次运行和 capital base 计算的收益率。",
                "Return derived from the latest run and its capital base."
              )}
            />
            <MetricCard
              label={txt("本地交易数", "Local Trades")}
              value={String(portfolio.transaction_count)}
              hint={txt(
                "本地 transactions 表里记录到这个 portfolio 的交易数。",
                "Number of local transactions recorded for this portfolio."
              )}
            />
            <MetricCard
              label={txt("净现金流", "Net Cash Flow")}
              value={formatMoney(portfolio.net_cash_flow, locale, brokerCurrency)}
              hint={txt(
                "本地交易记录聚合出来的净现金流。",
                "Net cash flow aggregated from local trade records."
              )}
            />
            <MetricCard
              label={txt("最近运行", "Latest Run")}
              value={portfolio.latest_run_status || "-"}
              hint={txt(
                "这个 portfolio 下最近一次策略运行的状态。",
                "Status of the latest strategy run under this portfolio."
              )}
            />
          </div>

          <div style={detailGridStyle}>
            {sectionCard(
              txt("组合信息", "Portfolio Identity"),
              txt(
                "这里聚合了 portfolio 自身的元数据，以及它和账户的绑定关系。",
                "This section summarizes the portfolio metadata and its account binding."
              ),
              <>
                {infoRow(
                  txt("Portfolio ID", "Portfolio ID"),
                  portfolio.id
                )}
                {infoRow(
                  txt("所属账户", "Owning Account"),
                  workspace?.account.name || "-"
                )}
                {infoRow(
                  txt("账户状态", "Account Status"),
                  workspace?.account.status || "-"
                )}
                {infoRow(
                  txt("组合状态", "Portfolio Status"),
                  <Badge tone={portfolio.status === "active" ? "success" : "warning"}>
                    {portfolio.status}
                  </Badge>
                )}
                {infoRow(
                  txt("创建时间", "Created At"),
                  formatDateTime(portfolioMeta?.created_at, locale)
                )}
                {infoRow(
                  txt("更新时间", "Updated At"),
                  formatDateTime(portfolioMeta?.updated_at, locale)
                )}
                {infoRow(
                  txt("最近交易", "Latest Trade"),
                  formatDateTime(portfolio.latest_transaction_at, locale)
                )}
              </>
            )}

            {sectionCard(
              txt("说明", "Description"),
              txt(
                "portfolio 的用途、风险桶或策略主题可以在这里快速确认。",
                "Quickly review this portfolio's role, risk bucket, or strategy theme."
              ),
              <p style={bodyTextStyle}>
                {portfolio.description || txt("这个 portfolio 还没有说明。", "This portfolio does not have a description yet.")}
              </p>
            )}
          </div>

          {sectionCard(
            txt("策略配置", "Strategy Setup"),
            txt(
              "每条策略都展示 allocation、日调度开关，以及它最近一次运行状态。",
              "Each strategy shows its allocation, daily auto-run flag, and latest run status."
            ),
            <div style={tableWrapStyle}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={headCellStyle}>{txt("策略", "Strategy")}</th>
                    <th style={headCellStyle}>{txt("状态", "Status")}</th>
                    <th style={headCellStyle}>{txt("日调度", "Auto-Run")}</th>
                    <th style={headCellStyle}>{txt("配比", "Allocation")}</th>
                    <th style={headCellStyle}>{txt("固定本金", "Capital Base")}</th>
                    <th style={headCellStyle}>{txt("最近运行", "Latest Run")}</th>
                    <th style={headCellStyle}>{txt("最近请求", "Requested At")}</th>
                    <th style={headCellStyle}>{txt("最近权益", "Latest Equity")}</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolio.strategies.map((item) => (
                    <tr key={item.strategy_id}>
                      <td style={bodyCellStyle}>
                        <div style={{ display: "grid", gap: 6 }}>
                          <Link
                            href={`/strategies/${item.strategy_id}`}
                            style={strategyLinkStyle}
                          >
                            {item.strategy_name}
                          </Link>
                          <div style={smallMutedTextStyle}>{item.strategy_type}</div>
                        </div>
                      </td>
                      <td style={bodyCellStyle}>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          <Badge
                            tone={item.strategy_status === "active" ? "success" : "warning"}
                          >
                            {item.strategy_status}
                          </Badge>
                          <Badge
                            tone={item.allocation_status === "active" ? "success" : "warning"}
                          >
                            {item.allocation_status}
                          </Badge>
                        </div>
                      </td>
                      <td style={bodyCellStyle}>
                        <Badge tone={item.auto_run_enabled ? "info" : "neutral"}>
                          {item.auto_run_enabled
                            ? txt("开启", "On")
                            : txt("关闭", "Off")}
                        </Badge>
                      </td>
                      <td style={bodyCellStyle}>
                        {formatPercent(item.allocation_pct, 2)}
                      </td>
                      <td style={bodyCellStyle}>
                        {formatMoney(item.capital_base, locale, brokerCurrency)}
                      </td>
                      <td style={bodyCellStyle}>{item.latest_run_status || "-"}</td>
                      <td style={bodyCellStyle}>
                        {formatDateTime(item.latest_run_requested_at, locale)}
                      </td>
                      <td style={bodyCellStyle}>
                        {formatMoney(item.latest_run_equity, locale, brokerCurrency)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {sectionCard(
            txt("最近本地交易", "Recent Local Transactions"),
            txt(
              "这里展示这个 portfolio 最近被工作台聚合出来的本地交易记录。",
              "These are the recent local transactions aggregated for this portfolio."
            ),
            <>
              <div style={tableMetaStyle}>
                {portfolio.transaction_count > portfolioTransactions.length
                  ? txt(
                      `当前显示最近 ${portfolioTransactions.length} 条，历史总数 ${portfolio.transaction_count} 条。`,
                      `Showing the latest ${portfolioTransactions.length} items out of ${portfolio.transaction_count} total local trades.`
                    )
                  : txt(
                      `当前显示 ${portfolioTransactions.length} 条本地交易记录。`,
                      `Showing ${portfolioTransactions.length} local trade records.`
                    )}
              </div>
              {portfolioTransactions.length === 0 ? (
                <div style={emptyStateStyle}>
                  {txt("这个 portfolio 还没有本地交易记录。", "There are no local transactions for this portfolio yet.")}
                </div>
              ) : (
                <div style={tableWrapStyle}>
                  <table style={tableStyle}>
                    <thead>
                      <tr>
                        <th style={headCellStyle}>{txt("时间", "Time")}</th>
                        <th style={headCellStyle}>{txt("策略", "Strategy")}</th>
                        <th style={headCellStyle}>{txt("标的", "Symbol")}</th>
                        <th style={headCellStyle}>{txt("方向", "Side")}</th>
                        <th style={headCellStyle}>{txt("数量", "Qty")}</th>
                        <th style={headCellStyle}>{txt("价格", "Price")}</th>
                        <th style={headCellStyle}>{txt("净现金流", "Net Cash Flow")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {portfolioTransactions.map((item) => (
                        <tr key={item.id}>
                          <td style={bodyCellStyle}>{formatDateTime(item.ts, locale)}</td>
                          <td style={bodyCellStyle}>
                            <Link
                              href={`/strategies/${item.strategy_id}`}
                              style={strategyLinkStyle}
                            >
                              {item.strategy_name || item.strategy_id}
                            </Link>
                          </td>
                          <td style={bodyCellStyle}>{item.symbol}</td>
                          <td style={bodyCellStyle}>{item.side}</td>
                          <td style={bodyCellStyle}>
                            {formatNumber(item.qty, locale, 4)}
                          </td>
                          <td style={bodyCellStyle}>
                            {formatMoney(item.price, locale, brokerCurrency)}
                          </td>
                          <td
                            style={{
                              ...bodyCellStyle,
                              color: item.net_cash_flow >= 0 ? "#34d399" : "#fb7185",
                              fontWeight: 700,
                            }}
                          >
                            {formatMoney(item.net_cash_flow, locale, brokerCurrency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      ) : null}
    </AppShell>
  );
}

const headerButtonStyle: CSSProperties = {
  padding: "11px 16px",
  borderRadius: 14,
  border: "1px solid rgba(148, 163, 184, 0.16)",
  background: "rgba(15, 23, 42, 0.72)",
  color: "#dbeafe",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const metricGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 16,
};

const detailGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
  gap: 18,
  alignItems: "start",
};

const sectionCardStyle: CSSProperties = {
  padding: 22,
  borderRadius: 24,
  border: "1px solid rgba(71, 85, 105, 0.3)",
  background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
  color: "#e2e8f0",
  boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
};

const errorPanelStyle: CSSProperties = {
  ...sectionCardStyle,
  border: "1px solid rgba(248, 113, 113, 0.24)",
  background: "rgba(127, 29, 29, 0.22)",
  color: "#fecaca",
};

const sectionTitleStyle: CSSProperties = {
  margin: "0 0 8px",
  fontSize: 24,
  color: "#f8fafc",
};

const sectionSubtitleStyle: CSSProperties = {
  margin: 0,
  color: "rgba(148, 163, 184, 0.88)",
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const infoRowStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "120px minmax(0, 1fr)",
  gap: 12,
  padding: "10px 0",
  borderBottom: "1px solid rgba(71, 85, 105, 0.28)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const infoLabelStyle: CSSProperties = {
  color: "rgba(148, 163, 184, 0.88)",
  fontWeight: 600,
};

const infoValueStyle: CSSProperties = {
  color: "#e2e8f0",
  wordBreak: "break-word",
};

const bodyTextStyle: CSSProperties = {
  margin: 0,
  color: "#e2e8f0",
  lineHeight: 1.7,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const tableWrapStyle: CSSProperties = {
  overflowX: "auto",
};

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  minWidth: 880,
};

const headCellStyle: CSSProperties = {
  padding: "10px 12px",
  borderBottom: "1px solid rgba(71, 85, 105, 0.4)",
  textAlign: "left",
  color: "#cbd5e1",
  fontSize: 13,
  fontWeight: 700,
  letterSpacing: "0.02em",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const bodyCellStyle: CSSProperties = {
  padding: "12px",
  borderBottom: "1px solid rgba(71, 85, 105, 0.22)",
  color: "#e2e8f0",
  verticalAlign: "top",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const strategyLinkStyle: CSSProperties = {
  color: "#7dd3fc",
  textDecoration: "none",
  fontWeight: 700,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const smallMutedTextStyle: CSSProperties = {
  color: "rgba(148, 163, 184, 0.82)",
  fontSize: 12,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const tableMetaStyle: CSSProperties = {
  marginBottom: 12,
  color: "rgba(148, 163, 184, 0.88)",
  fontSize: 13,
  lineHeight: 1.6,
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};

const emptyStateStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  border: "1px dashed rgba(148, 163, 184, 0.26)",
  color: "rgba(203, 213, 225, 0.82)",
  fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
};
