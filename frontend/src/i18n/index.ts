import { enUSMessages } from "./messages/en-US";
import { zhCNMessages } from "./messages/zh-CN";

export const MESSAGES = {
  "zh-CN": zhCNMessages,
  "en-US": enUSMessages,
} as const;

export const DEFAULT_LOCALE = "zh-CN";

export type Locale = keyof typeof MESSAGES;
export type Messages = (typeof MESSAGES)[Locale];
