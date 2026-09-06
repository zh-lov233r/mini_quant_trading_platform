import { afterEach, describe, expect, it, vi } from "vitest";

import { readApiError } from "./client";

function setTestLocale(locale: "zh-CN" | "en-US") {
  vi.stubGlobal("window", {
    localStorage: { getItem: () => locale },
    navigator: { language: locale },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readApiError", () => {
  it("explains missing Signal Center schema in both languages", async () => {
    for (const [locale,message] of [["zh-CN","信号中心数据库表尚未部署"],["en-US","Signal Center schema is not installed"]] as const) {
      setTestLocale(locale);
      const error = await readApiError(new Response(JSON.stringify({detail:{code:"signal_schema_missing",missing_tables:["signal_reports"]}}),{status:503}),"/api/signal-reports");
      expect(error.status).toBe(503);
      expect(error.message).toContain(message);
    }
  });
  it("localizes known 409 conflicts without changing ApiError fields", async () => {
    setTestLocale("en-US");
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
    setTestLocale("zh-CN");

    await expect(
      readApiError(new Response("", { status: 404 }), "/api/strategies/missing"),
    ).resolves.toMatchObject({ message: "请求的资源不存在。", status: 404 });
    await expect(
      readApiError(new Response("", { status: 422 }), "/api/strategies"),
    ).resolves.toMatchObject({ message: "提交内容未通过校验，请检查输入。", status: 422 });
  });
});
