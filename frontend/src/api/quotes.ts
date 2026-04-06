import http from "@/api/client";
import type { CandleSeriesOut } from "@/types/quote";

interface CandleSeriesParams {
  symbol: string;
  start_date: string;
  end_date: string;
  adjusted?: boolean;
}

const candleSeriesRequestCache = new Map<string, Promise<CandleSeriesOut>>();

export function getCandleSeries({
  symbol,
  start_date,
  end_date,
  adjusted = false,
}: CandleSeriesParams): Promise<CandleSeriesOut> {
  const query = new URLSearchParams({
    symbol,
    start_date,
    end_date,
    adjusted: adjusted ? "true" : "false",
  });
  const cacheKey = `${symbol.toUpperCase()}|${start_date}|${end_date}|${adjusted ? "1" : "0"}`;
  const cached = candleSeriesRequestCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  const request = http<CandleSeriesOut>(`/api/market-data/candles?${query.toString()}`, {
    method: "GET",
  }).catch((error) => {
    candleSeriesRequestCache.delete(cacheKey);
    throw error;
  });

  candleSeriesRequestCache.set(cacheKey, request);
  return request;
}
