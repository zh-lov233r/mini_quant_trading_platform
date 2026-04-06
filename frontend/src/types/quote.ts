export interface CandleBarOut {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
}

export interface CandleSeriesOut {
  symbol: string;
  adjusted: boolean;
  start_date: string;
  end_date: string;
  bar_count: number;
  bars: CandleBarOut[];
}
