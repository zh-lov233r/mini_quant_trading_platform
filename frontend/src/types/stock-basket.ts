export interface StockBasketCreate {
  name: string;
  description?: string | null;
  symbols: string[];
  status?: string;
}

export interface StockBasketOut {
  id: string;
  name: string;
  description?: string | null;
  symbols: string[];
  status: string;
  symbol_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}
