export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(message: string, status: number, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function mapConflictMessage(detail: string, path: string): string {
  const normalized = detail.toLowerCase();

  if (normalized.includes("paper account name already exists")) {
    return "这个 Paper Account 名称已经存在，请换一个新的账户名。";
  }
  if (normalized.includes("strategy portfolio name already exists")) {
    return "这个策略组合名称已经存在，请换一个新的组合名。当前第一版要求组合名全局唯一。";
  }
  if (normalized.includes("target strategy name already exists")) {
    return "这个策略名称已经被占用，请换一个全新的名字。当前版本不支持直接并入另一个已有策略族。";
  }
  if (
    normalized.includes("duplicate key") ||
    normalized.includes("already exists") ||
    normalized.includes("unique constraint")
  ) {
    if (path.includes("/api/strategies")) {
      return "检测到策略名称重复或版本冲突，请换一个名称，或确认是不是重复提交了同一条策略。";
    }
    return "检测到名称重复或资源冲突，请修改后重试。";
  }

  return `保存失败：${detail}`;
}

function mapApiErrorMessage(status: number, detail: string, path: string): string {
  const normalizedDetail = detail.trim();

  if (status === 409) {
    return mapConflictMessage(normalizedDetail, path);
  }

  if (status === 404) {
    return normalizedDetail || "请求的资源不存在。";
  }

  if (status === 422) {
    return normalizedDetail || "提交内容未通过校验，请检查输入。";
  }

  if (normalizedDetail) {
    return normalizedDetail;
  }

  return `${status} 请求失败`;
}

export async function readApiError(res: Response, path: string): Promise<ApiError> {
  const raw = await res.text().catch(() => "");
  let detail = raw.trim();

  if (detail) {
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        detail = parsed.detail.trim();
      }
    } catch {
      // keep original response text
    }
  }

  return new ApiError(
    mapApiErrorMessage(res.status, detail, path),
    res.status,
    detail || undefined
  );
}

async function http<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    throw await readApiError(res, path);
  }
  return res.json() as Promise<T>;
}

export default http;
