import { enUSMessages } from "./messages/en-US";
import { zhCNMessages } from "./messages/zh-CN";

export const MESSAGES = {
  "zh-CN": zhCNMessages,
  "en-US": enUSMessages,
} as const;

export const DEFAULT_LOCALE = "zh-CN";
export const LOCALE_STORAGE_KEY = "quant-trading-system.locale";

export type Locale = keyof typeof MESSAGES;
export type Messages = (typeof MESSAGES)[Locale];

export function detectLocale(): Locale {
  if (typeof window === "undefined") {
    return DEFAULT_LOCALE;
  }

  const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
  if (stored === "zh-CN" || stored === "en-US") {
    return stored;
  }

  return window.navigator.language.toLowerCase().startsWith("en") ? "en-US" : "zh-CN";
}

export function translate(
  locale: Locale,
  key: string,
  values?: Record<string, string | number>,
): string {
  const template = key.split(".").reduce<unknown>((current, segment) => {
    if (!current || typeof current !== "object") {
      return null;
    }
    return (current as Record<string, unknown>)[segment];
  }, MESSAGES[locale]);

  if (typeof template !== "string") {
    return key;
  }
  if (!values) {
    return template;
  }
  return Object.entries(values).reduce(
    (current, [name, value]) => current.replaceAll(`{${name}}`, String(value)),
    template,
  );
}
