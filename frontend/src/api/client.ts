import { detectLocale, translate, type Locale } from "@/i18n";

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

function mapConflictMessage(detail: string, path: string, locale: Locale): string {
  const normalized = detail.toLowerCase();

  if (normalized.includes("paper account name already exists")) {
    return translate(locale, "apiErrors.paperAccountNameExists");
  }
  if (normalized.includes("strategy portfolio name already exists")) {
    return translate(locale, "apiErrors.portfolioNameExists");
  }
  if (normalized.includes("target strategy name already exists")) {
    return translate(locale, "apiErrors.strategyNameExists");
  }
  if (
    normalized.includes("duplicate key") ||
    normalized.includes("already exists") ||
    normalized.includes("unique constraint")
  ) {
    if (path.includes("/api/strategies")) {
      return translate(locale, "apiErrors.strategyConflict");
    }
    return translate(locale, "apiErrors.resourceConflict");
  }

  return translate(locale, "apiErrors.saveFailed", { detail });
}

function mapApiErrorMessage(status: number, detail: string, path: string): string {
  const normalizedDetail = detail.trim();
  const locale = detectLocale();

  if (status === 409) {
    return mapConflictMessage(normalizedDetail, path, locale);
  }

  if (status === 404) {
    return normalizedDetail || translate(locale, "apiErrors.notFound");
  }

  if (status === 422) {
    return normalizedDetail || translate(locale, "apiErrors.validationFailed");
  }

  if (normalizedDetail) {
    return normalizedDetail;
  }

  return translate(locale, "apiErrors.requestFailed", { status });
}

export async function readApiError(res: Response, path: string): Promise<ApiError> {
  const raw = await res.text().catch(() => "");
  let detail = raw.trim();

  if (detail) {
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        detail = parsed.detail.trim();
      } else if (
        parsed.detail &&
        typeof parsed.detail === "object" &&
        "message" in parsed.detail &&
        typeof parsed.detail.message === "string"
      ) {
        detail = parsed.detail.message.trim();
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
