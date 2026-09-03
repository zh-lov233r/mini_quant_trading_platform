import { beforeEach, describe, expect, it, vi } from "vitest";
import http from "@/api/client";
import { getDashboardOverview } from "./dashboard";
import type { DashboardOverview } from "@/types/dashboard";

vi.mock("@/api/client", () => ({ default: vi.fn() }));
beforeEach(() => vi.mocked(http).mockReset());
describe("dashboard overview", () => {
  it("shares in-flight work and refreshes after completion", async () => {
    let resolve!: (value: DashboardOverview) => void;
    vi.mocked(http).mockImplementation(() => new Promise<DashboardOverview>(r => { resolve = r; }));
    const first = getDashboardOverview();
    const second = getDashboardOverview();
    expect(second).toBe(first);
    expect(http).toHaveBeenCalledExactlyOnceWith("/api/dashboard/overview", { method: "GET" });
    resolve({ generated_at: "2026-09-03T00:00:00Z" } as DashboardOverview);
    await first;
    vi.mocked(http).mockResolvedValue({});
    await getDashboardOverview();
    expect(http).toHaveBeenCalledTimes(2);
  });
  it("releases a failed request so retry can succeed", async () => {
    vi.mocked(http).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ generated_at: "retry" });
    await expect(getDashboardOverview()).rejects.toThrow("offline");
    await expect(getDashboardOverview()).resolves.toEqual({ generated_at: "retry" });
  });
});
