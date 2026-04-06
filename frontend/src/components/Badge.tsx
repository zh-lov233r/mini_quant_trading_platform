import type { CSSProperties, ReactNode } from "react";

type BadgeTone = "neutral" | "success" | "warning" | "info";

const TONE_STYLES: Record<BadgeTone, CSSProperties> = {
  neutral: {
    background: "rgba(51, 65, 85, 0.78)",
    color: "#e2e8f0",
  },
  success: {
    background: "rgba(20, 83, 45, 0.78)",
    color: "#bbf7d0",
  },
  warning: {
    background: "rgba(120, 53, 15, 0.78)",
    color: "#fde68a",
  },
  info: {
    background: "rgba(30, 64, 175, 0.72)",
    color: "#bfdbfe",
  },
};

interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
  style?: CSSProperties;
}

export default function Badge({ children, tone = "neutral", style }: BadgeProps) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "6px 11px",
        borderRadius: 999,
        fontSize: 13,
        fontWeight: 700,
        lineHeight: 1,
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        ...TONE_STYLES[tone],
        ...style,
      }}
    >
      {children}
    </span>
  );
}
