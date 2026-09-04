export type StockMarket = "US" | "CN";

export interface StockFilters {
  market?: StockMarket;
  query?: string;
  industry?: string;
  min_cap?: number;
  max_cap?: number;
}

export interface StockCandidate {
  instrument_id: number;
  ticker: string;
  name: string | null;
  market: StockMarket;
  industry: string | null;
  industry_source: "SIC" | "Tushare";
  market_cap: number | null;
  currency: string;
  cap_source: string | null;
  cap_data_date: string | null;
  cap_retrieved_at: string | null;
}

export interface StockSearchResult {
  items: StockCandidate[];
  total: number;
  missing_market_cap: number;
}
