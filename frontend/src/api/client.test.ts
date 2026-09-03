import { afterEach, describe, expect, it, vi } from "vitest";

import { readApiError } from "./client";

function useLocale(locale: "zh-CN" | "en-US") {
  vi.stubGlobal("window", {
    localStorage: { getItem: () => locale },
    navigator: { language: locale },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readApiError", () => {
  it("localizes known 409 conflicts without changing ApiError fields", async () => {
    useLocale("en-US");
    const error = await readApiError(
      new Response(JSON.stringify({ detail: "paper account name already exists" }), {
        status: 409,
      }),
      "/api/paper-accounts",
    );

    expect(error).toMatchObject({
      status: 409,
      detail: "paper account name already exists",
      message: "This Paper Account name already exists. Choose a new account name.",
    });
  });

  it("uses Chinese fallbacks for empty 404 and 422 responses", async () => {
    useLocale("zh-CN");

    await expect(
      readApiError(new Response("", { status: 404 }), "/api/strategies/missing"),
    ).resolves.toMatchObject({ message: "请求的资源不存在。", status: 404 });
    await expect(
      readApiError(new Response("", { status: 422 }), "/api/strategies"),
    ).resolves.toMatchObject({ message: "提交内容未通过校验，请检查输入。", status: 422 });
  });
});
