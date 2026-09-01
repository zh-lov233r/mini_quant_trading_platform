import { afterEach, describe, expect, it, vi } from "vitest";

import { deleteBacktest, getBacktestSupportResistance } from "./backtests";
import { deleteResearchBacktest } from "./research";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("backtest deletion clients", () => {
  it("deletes one manual backtest with an encoded run id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ run_id: "run/id", deleted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteBacktest("run/id")).resolves.toEqual({ run_id: "run/id", deleted: true });

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/backtests/run%2Fid");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
  });

  it("deletes research backtests only through their owning experiment", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ run_id: "run/id", deleted: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await deleteResearchBacktest("experiment/id", "run/id");

    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/research/experiments/experiment%2Fid/backtests/run%2Fid",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
  });
});

describe("support/resistance client cache", () => {
  const completedDetail = {
    run_id: "run-cache",
    materialization: { status: "completed" },
    zone_versions: [],
    regime_intervals: [],
    events: [],
  };

  it("reuses an identical completed window regardless of filter insertion order", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(completedDetail),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getBacktestSupportResistance("run-cache", {
      symbol: "ATS",
      start_date: "2026-05-26",
      end_date: "2026-07-31",
    });
    await getBacktestSupportResistance("run-cache", {
      end_date: "2026-07-31",
      symbol: "ATS",
      start_date: "2026-05-26",
    });

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("keeps different visible windows separate", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({ ...completedDetail, run_id: "run-windows" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await getBacktestSupportResistance("run-windows", { symbol: "ATS", start_date: "2026-06-01" });
    await getBacktestSupportResistance("run-windows", { symbol: "ATS", start_date: "2026-07-01" });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not retain incomplete or failed requests", async () => {
    const incompleteFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        ...completedDetail,
        run_id: "run-building",
        materialization: { status: "building" },
      }),
    });
    vi.stubGlobal("fetch", incompleteFetch);
    await getBacktestSupportResistance("run-building", { symbol: "ATS" });
    await getBacktestSupportResistance("run-building", { symbol: "ATS" });
    expect(incompleteFetch).toHaveBeenCalledTimes(2);

    const failedFetch = vi.fn().mockResolvedValue({
      ok: false,
      text: vi.fn().mockResolvedValue("temporary failure"),
    });
    vi.stubGlobal("fetch", failedFetch);
    await expect(getBacktestSupportResistance("run-failure", { symbol: "ATS" })).rejects.toThrow();
    await expect(getBacktestSupportResistance("run-failure", { symbol: "ATS" })).rejects.toThrow();
    expect(failedFetch).toHaveBeenCalledTimes(2);
  });
});
