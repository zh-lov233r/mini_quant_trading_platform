import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { getStockIndustries, resolveStockSymbols, searchStocks } from "@/api/stock-screening";
import { useI18n } from "@/i18n/provider";
import { stockBasketMessages } from "@/i18n/messages/stock-baskets";
import type { StockFilters, StockMarket, StockSearchResult } from "@/types/stock-screening";
import { addBasketSymbols, removeBasketSymbols, selectedSymbolPage, singleTicker } from "@/utils/stockBasketSelection";
import styles from "./StockBasketSelector.module.css";

export const StockBasketSelector = memo(function StockBasketSelector({ symbols, onChange }: {
  symbols: string[]; onChange: Dispatch<SetStateAction<string[]>>;
}) {
  const { locale } = useI18n();
  const t = stockBasketMessages[locale];
  const [mode, setMode] = useState<"manual" | "filter">("manual");
  const [query, setQuery] = useState("");
  const [market, setMarket] = useState<StockMarket | "">("");
  const [industry, setIndustry] = useState("");
  const [industries, setIndustries] = useState<string[]>([]);
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const [page, setPage] = useState(0);
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<StockSearchResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [pending, setPending] = useState<string[] | null>(null);
  const resolveController = useRef<AbortController | null>(null);
  const [selectedQuery, setSelectedQuery] = useState("");
  const [selectedPage, setSelectedPage] = useState(0);
  const [checked, setChecked] = useState<string[]>([]);
  const selected = useMemo(() => new Set(symbols), [symbols]);
  const selection = useMemo(() => selectedSymbolPage(symbols, selectedQuery, selectedPage), [symbols, selectedQuery, selectedPage]);
  const ticker = singleTicker(query);
  const rangeInvalid = mode === "filter" && ((min !== "" && (!Number.isFinite(Number(min)) || Number(min) < 0 || Number(min) >= 1e12))
    || (max !== "" && (!Number.isFinite(Number(max)) || Number(max) <= 0 || Number(max) >= 1e12))
    || (min !== "" && max !== "" && Number(min) > Number(max)));
  const filters = useMemo<StockFilters>(() => ({ query: query.trim(), market: market || undefined,
    industry: mode === "filter" ? industry || undefined : undefined,
    min_cap: mode === "filter" && market && min !== "" ? Number(min) * 1e8 : undefined,
    max_cap: mode === "filter" && market && max !== "" ? Number(max) * 1e8 : undefined,
  }), [query, market, mode, industry, min, max]);

  useEffect(() => {
    const controller = new AbortController();
    setIndustries([]);
    if (market && mode === "filter") getStockIndustries(market, controller.signal)
      .then((items) => { if (!controller.signal.aborted) setIndustries(items); })
      .catch(() => { if (!controller.signal.aborted) setError(t.failed); });
    return () => controller.abort();
  }, [market, mode, retry, t.failed]);

  useEffect(() => {
    const controller = new AbortController();
    resolveController.current?.abort();
    setPending(null); setResolving(false); setResult(null); setError("");
    if (rangeInvalid || (mode === "manual" && !filters.query)) { setLoading(false); return; }
    setLoading(true);
    const timeout = setTimeout(() => {
      searchStocks(filters, page, controller.signal).then((items) => {
        if (!controller.signal.aborted) setResult(items);
      }).catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message || t.failed);
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 300);
    return () => { clearTimeout(timeout); controller.abort(); resolveController.current?.abort(); };
  }, [filters, page, mode, rangeInvalid, retry, t.failed]);

  const changeCriteria = () => { setPage(0); setResult(null); setPending(null); resolveController.current?.abort(); };
  const add = (items: string[]) => onChange((current) => addBasketSymbols(current, items));
  const remove = (items: string[]) => { onChange((current) => removeBasketSymbols(current, items)); setChecked([]); };
  const resolveAll = async () => {
    const controller = new AbortController();
    resolveController.current = controller;
    setResolving(true);
    try {
      const items = await resolveStockSymbols(filters, controller.signal);
      if (!controller.signal.aborted) setPending(items);
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : t.failed);
    } finally { if (!controller.signal.aborted) setResolving(false); }
  };
  const newCount = pending?.filter((symbol) => !selected.has(symbol)).length ?? 0;
  return <section className={styles.selector} aria-label={t.selected}>
    <div className={styles.row}>
      {(["manual", "filter"] as const).map((value) => <button key={value} type="button" aria-pressed={mode === value}
        onClick={() => { setMode(value); changeCriteria(); }}>{t[value]}</button>)}
    </div>
    <div className={styles.panel}>
      <input type="search" aria-label={t.search} placeholder={t.search} value={query} maxLength={100} spellCheck={false}
        onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
        onChange={(event) => { setQuery(event.target.value); changeCriteria(); }} />
      <div className={styles.fields}>
        <label>{t.market}<select value={market} onChange={(event) => {
          setMarket(event.target.value as StockMarket | ""); setIndustry(""); setMin(""); setMax(""); changeCriteria();
        }}><option value="">{t.allMarkets}</option><option value="CN">{t.cn}</option><option value="US">{t.us}</option></select></label>
        {mode === "filter" ? <>
          <label>{t.industry} {market ? `(${market === "CN" ? "Tushare" : "SIC"})` : ""}<select disabled={!market} value={industry}
            onChange={(event) => { setIndustry(event.target.value); changeCriteria(); }}>
            <option value="">{t.allIndustries}</option>{industries.map((value) => <option key={value}>{value}</option>)}
          </select></label>
          <label>{t.min} ({market === "CN" ? t.cny : t.usd})<input type="number" min="0" step="any" disabled={!market} value={min}
            onChange={(event) => { setMin(event.target.value); changeCriteria(); }} /></label>
          <label>{t.max} ({market === "CN" ? t.cny : t.usd})<input type="number" min="0" step="any" disabled={!market} value={max}
            onChange={(event) => { setMax(event.target.value); changeCriteria(); }} /></label>
        </> : null}
      </div>
      {mode === "filter" && !market ? <p className={styles.hint}>{t.chooseMarket}</p> : null}
      {mode === "manual" && ticker ? <div className={styles.row}>
        <button type="button" disabled={selected.has(ticker)} onClick={() => { add([ticker]); setQuery(""); changeCriteria(); }}>
          {selected.has(ticker) ? t.added : t.direct}: {ticker}</button>
        <p className={styles.hint}>{result && !loading && result.total === 0 && !market ? t.notListed : t.unverified}</p>
      </div> : null}
      {mode === "manual" && !query ? <p className={styles.hint}>{t.queryFirst}</p> : null}
      {rangeInvalid ? <p role="alert" className={styles.error}>{t.invalidRange}</p> : null}
      {error ? <div role="alert" className={styles.error}>{error} <button type="button" onClick={() => setRetry((value) => value + 1)}>{t.retry}</button></div> : null}
      {loading ? <p role="status" className={styles.hint}>{t.loading}</p> : null}
      {result ? <>
        <div className={styles.row}><span>{t.matches}: {result.total} · {t.missing}: {result.missing_market_cap}</span>
          {mode === "filter" ? <button type="button" disabled={!result.total || resolving} onClick={resolveAll}>{t.addAll}</button> : null}</div>
        <div className={styles.list}>
          {result.items.map((item) => <div className={styles.item} key={item.instrument_id}>
            <div className={styles.copy}><strong>{item.ticker}</strong>{item.name}
              <div className={styles.hint}>{item.industry_source}: {item.industry || t.unknown} · {t.cap}: {item.market_cap == null ? t.unknown : `${(item.market_cap / 1e8).toLocaleString(locale, { maximumFractionDigits: 2 })} ${item.currency === "CNY" ? t.cny : t.usd}`}</div>
              <div className={styles.hint}>{item.cap_source || "—"} · {t.dataDate}: {item.cap_data_date || t.unknown} · {t.retrieved}: {item.cap_retrieved_at ? new Date(item.cap_retrieved_at).toLocaleString(locale) : t.unknown}</div>
            </div><button type="button" disabled={selected.has(item.ticker)} onClick={() => add([item.ticker])}>{selected.has(item.ticker) ? t.added : t.add} {item.ticker}</button>
          </div>)}
          {!result.items.length ? <p className={styles.hint}>{t.noResults}</p> : null}
        </div>
        <div className={styles.row}><button type="button" disabled={!page} onClick={() => setPage(page - 1)}>{t.previous}</button>
          <span>{page + 1} / {Math.max(1, Math.ceil(result.total / 20))}</span>
          <button type="button" disabled={(page + 1) * 20 >= result.total} onClick={() => setPage(page + 1)}>{t.next}</button></div>
      </> : null}
      {resolving ? <p role="status">{t.resolving}</p> : null}
      {pending ? <div className={styles.panel} role="group" aria-label={t.confirm}>
        <span>{t.confirm}: {newCount} {t.stocks}. {t.append}</span><div className={styles.row}>
          <button type="button" disabled={!newCount} onClick={() => { add(pending); setPending(null); }}>{t.confirm}</button>
          <button type="button" onClick={() => setPending(null)}>{t.cancel}</button></div>
      </div> : null}
    </div>
    <div className={styles.panel}>
      <strong>{t.selected}: {symbols.length}</strong>
      <input type="search" aria-label={t.selectedSearch} placeholder={t.selectedSearch} value={selectedQuery}
        onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }}
        onChange={(event) => { setSelectedQuery(event.target.value); setSelectedPage(0); setChecked([]); }} />
      <div className={styles.row}><label className={styles.row}><input type="checkbox" checked={selection.items.length > 0 && selection.items.every((symbol) => checked.includes(symbol))}
        onChange={(event) => setChecked(event.target.checked ? selection.items : [])} />{t.checkPage}</label>
        <button type="button" disabled={!checked.length} onClick={() => remove(checked)}>{t.removeChecked} ({checked.length})</button></div>
      <div className={styles.list} data-testid="selected-symbols">
        {selection.items.map((symbol) => <div className={styles.item} key={symbol}>
          <label className={styles.row}><input type="checkbox" checked={checked.includes(symbol)} onChange={(event) => setChecked((current) => event.target.checked ? [...current, symbol] : current.filter((item) => item !== symbol))} />{symbol}</label>
          <button type="button" onClick={() => remove([symbol])}>{t.remove} {symbol}</button></div>)}
        {!selection.items.length ? <p className={styles.hint}>{symbols.length ? t.noResults : t.empty}</p> : null}
      </div>
      <div className={styles.row}><button type="button" disabled={!selection.page} onClick={() => { setSelectedPage(selection.page - 1); setChecked([]); }}>{t.previous}</button>
        <span>{selection.page + 1} / {selection.pages} · {selection.total} {t.stocks}</span>
        <button type="button" disabled={selection.page + 1 >= selection.pages} onClick={() => { setSelectedPage(selection.page + 1); setChecked([]); }}>{t.next}</button></div>
    </div>
    <p className={styles.hint}>{t.snapshot}</p>
  </section>;
});
