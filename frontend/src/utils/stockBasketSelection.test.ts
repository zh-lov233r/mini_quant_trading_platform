import { expect, it } from "vitest";
import { addBasketSymbols, removeBasketSymbols, selectedSymbolPage, singleTicker } from "./stockBasketSelection";

it("appends normalized symbols without replacing or mutating the saved basket", () => {
  const saved = ["AAPL", "600000.SH"];
  const draft = addBasketSymbols(saved, [" msft ", "aapl", "MSFT"]);
  expect(draft).toEqual(["AAPL", "600000.SH", "MSFT"]);
  expect(removeBasketSymbols(draft, ["AAPL", "MSFT"])).toEqual(["600000.SH"]);
  expect(saved).toEqual(["AAPL", "600000.SH"]);
});

it("bounds the rendered page, searches all symbols, and clamps after deletion", () => {
  const symbols = Array.from({ length: 5555 }, (_, i) => `${String(i).padStart(6, "0")}.SZ`);
  expect(selectedSymbolPage(symbols, "", 0).items).toHaveLength(20);
  expect(selectedSymbolPage(symbols, "005554.sz", 0).items).toEqual(["005554.SZ"]);
  expect(selectedSymbolPage(["AAPL"], "", 277)).toEqual({ items: ["AAPL"], total: 1, page: 0, pages: 1 });
  expect(selectedSymbolPage([], "", 1).page).toBe(0);
});

it("accepts one short ticker, not pasted multi-line lists or company names", () => {
  expect(singleTicker(" brk.b ")).toBe("BRK.B");
  expect(singleTicker("600000.sh")).toBe("600000.SH");
  expect(singleTicker("AAPL\nMSFT")).toBeNull();
  expect(singleTicker("Apple Inc")).toBeNull();
});
