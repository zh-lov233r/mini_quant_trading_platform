import { useEffect, useState } from "react";

import { useI18n } from "@/i18n/provider";

const VISIBILITY_THRESHOLD = 280;

export default function BackToTopButton() {
  const { messages } = useI18n();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    function syncVisibility() {
      setVisible(window.scrollY > VISIBILITY_THRESHOLD);
    }

    syncVisibility();
    window.addEventListener("scroll", syncVisibility, { passive: true });

    return () => {
      window.removeEventListener("scroll", syncVisibility);
    };
  }, []);

  const label = messages.common.backToTop;

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      style={{
        position: "fixed",
        right: 24,
        bottom: 24,
        zIndex: 1200,
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 16px",
        borderRadius: 999,
        border: "1px solid rgba(148, 163, 184, 0.24)",
        background: "rgba(8, 15, 24, 0.88)",
        color: "#f8fafc",
        boxShadow: "0 18px 42px rgba(2, 6, 23, 0.28)",
        backdropFilter: "blur(10px)",
        cursor: visible ? "pointer" : "default",
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? "auto" : "none",
        transform: visible ? "translateY(0)" : "translateY(12px)",
        transition: "opacity 180ms ease, transform 180ms ease",
        fontSize: 14,
        fontWeight: 700,
        fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
      }}
    >
      <svg
        viewBox="0 0 24 24"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 19V5" />
        <path d="m6 11 6-6 6 6" />
      </svg>
      <span>{label}</span>
    </button>
  );
}
