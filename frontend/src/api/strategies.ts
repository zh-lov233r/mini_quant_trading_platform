import type { StrategyCreate, StrategyOut } from "@/types/strategy";

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

/**
 * 创建策略：把 JSON 对象发到 /api/v1/strategies
 * 注意：只做一次 JSON.stringify
 */
export async function createStrategy(
  payload: StrategyCreate,
  idempotencyKey?: string
): Promise<StrategyOut> {
  const res = await fetch(`${BASE}/api/strategies`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
    },
    body: JSON.stringify(payload), //只stringify一次
  });

  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${txt}`.trim());
  }
  return res.json() as Promise<StrategyOut>;
}

