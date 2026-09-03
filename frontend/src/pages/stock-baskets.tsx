import type { CSSProperties, FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";

import { createStockBasket, listStockBaskets, updateStockBasket } from "@/api/stock-baskets";
import AppShell, { PageActionLink } from "@/components/AppShell";
import Badge from "@/components/Badge";
import MetricCard from "@/components/MetricCard";
import { SelectControl } from "@/components/workspace/SelectControl";
import { WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";
import type { StockBasketCreate, StockBasketOut } from "@/types/stock-basket";
import { formatDateTime } from "@/utils/strategy";

function parseSymbols(raw: string): string[] {
  return raw
    .split(/[\s,，]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function formatSymbolPreview(symbols: string[], limit = 20): string {
  if (symbols.length <= limit) {
    return symbols.join(", ");
  }
  return `${symbols.slice(0, limit).join(", ")} ... +${symbols.length - limit}`;
}

export function StockBasketForm({ basket, onSaved }: { basket: StockBasketOut | null; onSaved: (basket: StockBasketOut) => void }) {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [name, setName] = useState(basket?.name ?? "");
  const [description, setDescription] = useState(basket?.description ?? "");
  const [symbolsText, setSymbolsText] = useState(() => basket?.symbols.join("\n") ?? "");
  const [editingSymbols, setEditingSymbols] = useState(!basket);
  const [status, setStatus] = useState(basket?.status ?? "active");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const symbols = useMemo(() => parseSymbols(symbolsText), [symbolsText]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitError(null);
    if (!name.trim()) {
      setSubmitError(isZh ? "请输入股票组合名称" : "Please enter a basket name");
      return;
    }
    if (symbols.length === 0) {
      setSubmitError(isZh ? "请至少输入一个股票代码" : "Please enter at least one symbol");
      return;
    }

    const payload: StockBasketCreate = {
      name: name.trim(),
      description: description.trim() || null,
      symbols,
      status,
    };

    try {
      setSubmitting(true);
      const created = basket ? await updateStockBasket(basket.id, payload) : await createStockBasket(payload);
      onSaved(created);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : (isZh ? "保存股票组合失败" : "Failed to save the stock basket"));
    } finally {
      setSubmitting(false);
    }
  };


  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>
          {basket ? (isZh ? "编辑股票组合" : "Edit Stock Basket") : (isZh ? "创建股票组合" : "Create Stock Basket")}
        </h2>
        <p style={subtitleStyle}>
          {isZh
            ? "组合可以是主题篮子、行业观察池、白名单或者你自己的高 conviction list"
            : "A basket can be a thematic list, sector watchlist, whitelist, or your own high-conviction set."}
        </p>
      </div>

      <form onSubmit={handleSubmit} style={{ display: "grid", gap: 12 }}>
        <input
          aria-label={isZh ? "组合名称" : "Basket name"}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={isZh ? "组合名称，例如 Mega Cap Core" : "Basket name, for example Mega Cap Core"}
          style={inputStyle}
        />
        <textarea
          aria-label={isZh ? "组合说明" : "Basket description"}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={
            isZh
              ? "描述这个组合的用途，例如：用于趋势策略的美股核心科技篮子"
              : "Describe what this basket is for, for example a US large-cap tech core basket for trend strategies"
          }
          rows={4}
          style={{ ...inputStyle, resize: "vertical" }}
        />
        {basket ? (
          <button
            type="button"
            aria-expanded={editingSymbols}
            aria-controls="basket-symbols"
            onClick={() => setEditingSymbols((open) => !open)}
            style={{ ...buttonStyle, justifySelf: "start", minHeight: 44 }}
          >
            {editingSymbols
              ? (isZh ? "收起股票代码" : "Collapse Symbols")
              : (isZh ? `编辑股票代码（${symbols.length} 只）` : `Edit Symbols (${symbols.length})`)}
          </button>
        ) : null}
        {/* Mount large editable text only on demand: Safari AX text reads can synchronously spell-check it. */}
        {editingSymbols ? <textarea
          id="basket-symbols"
          aria-label={isZh ? "股票代码" : "Symbols"}
          value={symbolsText}
          onChange={(e) => setSymbolsText(e.target.value)}
          placeholder={
            isZh
              ? "输入股票代码，用空格、换行或逗号分隔"
              : "Enter symbols separated by spaces, new lines, or commas"
          }
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="none"
          rows={6}
          style={{ ...inputStyle, resize: "vertical" }}
        /> : null}
        <SelectControl
          aria-label={isZh ? "股票组合状态" : "Basket status"}
          value={status}
          onValueChange={setStatus}
          options={[
            { value: "active", label: "active" },
            { value: "draft", label: "draft" },
            { value: "archived", label: "archived" },
          ]}
        />

        <div
          style={{
            padding: 14,
            borderRadius: 16,
            background: "rgba(15, 23, 42, 0.72)",
            border: "1px solid rgba(71, 85, 105, 0.3)",
            color: "rgba(148, 163, 184, 0.9)",
            lineHeight: 1.6,
            fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
            fontSize: 13,
          }}
        >
          {isZh ? "预览" : "Preview"}: {symbols.slice(0, 12).join(", ")}
          {symbols.length > 12
            ? ` +${symbols.length - 12}`
            : ""}
        </div>

        <button type="submit" disabled={submitting} style={buttonStyle}>
          {submitting ? (isZh ? "保存中..." : "Saving...") : isZh ? "保存到股票库" : "Save Basket"}
        </button>
      </form>

      {submitError ? <p style={{ color: "#fda4af", marginTop: 12 }}>{submitError}</p> : null}
    </>
  );
}

export default function StockBasketsPage() {
  const { locale } = useI18n();
  const isZh = locale === "zh-CN";
  const [items, setItems] = useState<StockBasketOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingBasket, setEditingBasket] = useState<StockBasketOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    listStockBaskets()
      .then((baskets) => {
        if (!cancelled) {
          setItems(baskets);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) {
          setError(err.message || (isZh ? "加载股票库失败" : "Failed to load stock baskets"));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isZh]);

  const stats = useMemo(() => {
    const active = items.filter((item) => item.status === "active").length;
    const archived = items.filter((item) => item.status === "archived").length;
    const symbols = items.reduce((sum, item) => sum + item.symbol_count, 0);
    return {
      total: items.length,
      active,
      archived,
      avgSize: items.length > 0 ? (symbols / items.length).toFixed(1) : "0",
    };
  }, [items]);

  return (
    <AppShell
      title={isZh ? "股票库" : "Stock Baskets"}
      subtitle={
        isZh
          ? "把常用股票组合沉淀成可复用的库，回测时直接绑定到策略上，不用每次手动改 universe"
          : "Turn commonly used stock groups into a reusable library so backtests can bind them directly instead of editing the universe every time."
      }
      actions={
        <>
          <PageActionLink href="/backtests">{isZh ? "去回测" : "Open Backtests"}</PageActionLink>
          <PageActionLink href="/stock-baskets" primary>{isZh ? "刷新股票库" : "Refresh Baskets"}</PageActionLink>
        </>
      }
    >
      {loading ? <p>{isZh ? "加载中..." : "Loading..."}</p> : null}
      {error ? <p style={{ color: "#fda4af" }}>{error}</p> : null}

      {!loading && !error ? (
        <>
          <section
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 16,
              marginBottom: 20,
            }}
          >
            <MetricCard
              label={isZh ? "股票组合数" : "Basket Count"}
              value={String(stats.total)}
              hint={isZh ? "股票库里当前存了多少个可复用组合" : "How many reusable baskets are currently stored"}
              accent="#0f766e"
            />
            <MetricCard
              label="Active"
              value={String(stats.active)}
              hint={isZh ? "这些组合会直接出现在回测工作台的下拉框里" : "These baskets appear directly in the backtest selector"}
              accent="#2563eb"
            />
            <MetricCard
              label="Archived"
              value={String(stats.archived)}
              hint={isZh ? "历史上用过、但暂时不希望继续出现在默认选择里的组合" : "Baskets used before but currently hidden from default selection"}
              accent="#ca8a04"
            />
            <MetricCard
              label={isZh ? "平均规模" : "Average Size"}
              value={stats.avgSize}
              hint={isZh ? "平均每个组合里包含多少只股票" : "Average number of symbols per basket"}
              accent="#b45309"
            />
          </section>

          <WorkspaceDialog
            open={editorOpen}
            onOpenChange={(open) => {
              setEditorOpen(open);
              if (!open) return;
              setEditingBasket(null);
            }}
            triggerLabel={isZh ? "创建股票组合" : "Create Stock Basket"}
            title={editingBasket ? (isZh ? "编辑股票组合" : "Edit Stock Basket") : (isZh ? "创建股票组合" : "Create Stock Basket")}
            description={isZh ? "创建可供策略和回测复用的股票池。" : "Create a reusable stock universe for strategies and backtests."}
            size="form"
            triggerTone="primary"
          >
            {editorOpen ? (
              <StockBasketForm
                key={editingBasket?.id ?? "create"}
                basket={editingBasket}
                onSaved={(saved) => {
                  setItems((prev) => [saved, ...prev.filter((item) => item.id !== saved.id)]);
                  setEditorOpen(false);
                }}
              />
            ) : null}
          </WorkspaceDialog>

          <section style={cardStyle}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 12,
                  flexWrap: "wrap",
                  alignItems: "center",
                  marginBottom: 14,
                }}
              >
                <div>
                  <h2 style={{ margin: "0 0 8px", fontSize: 24 }}>
                    {isZh ? "已保存的组合" : "Saved Baskets"}
                  </h2>
                  <p style={subtitleStyle}>
                    {isZh
                      ? "回测工作台会优先列出这里状态为 `active` 的股票组合"
                      : "The backtest workspace prioritizes baskets here that are marked `active`."}
                  </p>
                </div>
              </div>

              {items.length === 0 ? (
                <div style={emptyStateStyle}>
                  {isZh
                    ? "还没有股票组合。先创建一个，回测时就能直接绑定到策略上"
                    : "No stock baskets yet. Create one first so backtests can bind it directly."}
                </div>
              ) : (
                <div style={{ display: "grid", gap: 14 }}>
                  {items.map((item) => (
                    <article key={item.id} style={listItemStyle}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 12,
                          flexWrap: "wrap",
                          marginBottom: 10,
                        }}
                      >
                        <div>
                          <h3 style={{ margin: "0 0 6px", fontSize: 20 }}>{item.name}</h3>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            <Badge tone={item.status === "active" ? "success" : "neutral"}>
                              {item.status}
                            </Badge>
                            <Badge tone="info">
                              {item.symbol_count} {isZh ? "symbols" : "symbols"}
                            </Badge>
                          </div>
                        </div>
                        <div style={{ color: "rgba(148, 163, 184, 0.88)", fontSize: 13, fontFamily: bodyFont }}>
                          {formatDateTime(item.updated_at || item.created_at, locale)}
                        </div>
                      </div>
                      <div style={{ color: "rgba(148, 163, 184, 0.88)", lineHeight: 1.6, marginBottom: 10, fontFamily: bodyFont }}>
                        {item.description?.trim() || (isZh ? "暂无说明" : "No description yet")}
                      </div>
                      <div style={{ color: "#e2e8f0", lineHeight: 1.7, fontFamily: bodyFont }}>
                        {formatSymbolPreview(item.symbols, 20)}
                      </div>
                      <div style={{ marginTop: 14 }}>
                        <button
                          type="button"
                          disabled={item.name === "All Common Stock"}
                          title={item.name === "All Common Stock" ? (isZh ? "系统维护组合；请新建独立组合" : "System-managed basket; create a separate basket") : undefined}
                          style={{ ...buttonStyle, minHeight: 44, opacity: item.name === "All Common Stock" ? 0.5 : 1 }}
                          onClick={() => {
                            setEditingBasket(item);
                            setEditorOpen(true);
                          }}
                        >{isZh ? "编辑" : "Edit"}</button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
          </section>
        </>
      ) : null}
    </AppShell>
  );
}

const bodyFont = "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif";

const cardStyle: CSSProperties = {
  padding: 22,
  borderRadius: 24,
  border: "1px solid rgba(71, 85, 105, 0.3)",
  background: "linear-gradient(180deg, rgba(8,15,24,0.92), rgba(15,23,42,0.88))",
  color: "#e2e8f0",
  boxShadow: "0 18px 44px rgba(2, 6, 23, 0.22)",
};

const subtitleStyle: CSSProperties = {
  margin: 0,
  color: "rgba(148, 163, 184, 0.88)",
  lineHeight: 1.6,
  fontFamily: bodyFont,
};

const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: 12,
  borderRadius: 14,
  border: "1px solid rgba(71, 85, 105, 0.34)",
  background: "rgba(8, 15, 24, 0.82)",
  fontSize: 14,
  color: "#e2e8f0",
  fontFamily: bodyFont,
};

const buttonStyle: CSSProperties = {
  padding: "12px 16px",
  borderRadius: 14,
  border: "none",
  background: "#0f766e",
  color: "#fff",
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: bodyFont,
};

const emptyStateStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  background: "rgba(15, 23, 42, 0.76)",
  color: "rgba(148, 163, 184, 0.88)",
  fontFamily: bodyFont,
};

const listItemStyle: CSSProperties = {
  padding: 18,
  borderRadius: 18,
  border: "1px solid rgba(71, 85, 105, 0.28)",
  background: "radial-gradient(circle at top right, rgba(45,212,191,0.08), transparent 24%), rgba(8, 15, 24, 0.88)",
  color: "#e2e8f0",
};
