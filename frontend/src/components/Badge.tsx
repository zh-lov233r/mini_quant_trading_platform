import type { CSSProperties, ReactNode } from "react";

type BadgeTone = "neutral" | "success" | "warning" | "info";

const TONE_STYLES: Record<BadgeTone, CSSProperties> = {
  neutral: {
    background: "#e2e8f0",
    color: "#334155",
  },
  success: {
    background: "#dcfce7",
    color: "#166534",
  },
  warning: {
    background: "#fef3c7",
    color: "#92400e",
  },
  info: {
    background: "#dbeafe",
    color: "#1d4ed8",
  },
};

interface BadgeProps {
  children: ReactNode;
  tone?: BadgeTone;
}

export default function Badge({ children, tone = "neutral" }: BadgeProps) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "5px 10px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 700,
        lineHeight: 1,
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        ...TONE_STYLES[tone],
      }}
    >
      {children}
    </span>
  );
}
