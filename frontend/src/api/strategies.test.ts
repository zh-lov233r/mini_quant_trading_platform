import { afterEach, describe, expect, it, vi } from "vitest";

import { cloneStrategy } from "./strategies";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("cloneStrategy", () => {
  it("posts only editable clone fields with an idempotency key", async () => {
    const response = {
      id: "clone-id",
      strategy_key: "Independent Copy",
      name: "Independent Copy",
      strategy_type: "trend",
      status: "draft",
      version: 1,
      params: {},
      engine_ready: true,
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue(response),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(cloneStrategy(
      "source/id",
      { name: "Independent Copy", description: "Edited", params: { signal: {} } },
      "clone-key",
    )).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, request] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/strategies/source%2Fid/clone");
    expect(request).toMatchObject({
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": "clone-key",
      },
    });
    expect(JSON.parse(request.body)).toEqual({
      name: "Independent Copy",
      description: "Edited",
      params: { signal: {} },
    });
  });
});
