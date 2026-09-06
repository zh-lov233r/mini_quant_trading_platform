import { beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("./client", () => ({ default: vi.fn().mockResolvedValue({}), API_BASE: "" }));
import http from "./client";
import { getSignalChart, startSignalScan } from "./signals";

beforeEach(() => vi.clearAllMocks());

describe("manual signal scans", () => {
  it("keeps the explicit enqueue request", async () => {
    const body = { name: "Manual", market: "US" as const, basket_id: "basket", strategy_ids: ["strategy"], session_date: null };
    await startSignalScan(body);
    expect(http).toHaveBeenCalledWith("/api/signal-scans", expect.objectContaining({
      method: "POST",
      body: JSON.stringify(body),
      headers: expect.objectContaining({ "Content-Type": "application/json", "Idempotency-Key": expect.any(String) }),
    }));
  });
});

describe("signal report charts", () => {
  it("deduplicates identical immutable snapshot requests", async () => {
    await getSignalChart("same-id", 1, "strategy", "signal", 120);
    await getSignalChart("same-id", 1, "strategy", "signal", 120);
    expect(http).toHaveBeenCalledTimes(1);
    expect(vi.mocked(http).mock.calls[0]?.[0]).toBe(
      "/api/signal-reports/same-id/instruments/1/chart?strategy_run_id=strategy&signal_id=signal&limit=120"
    );
  });
});
