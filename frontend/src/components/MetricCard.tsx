interface MetricCardProps {
  label: string;
  value: string;
  hint: string;
  accent?: string;
  labelFontSize?: number;
  valueFontSize?: number;
  hintFontSize?: number;
}

export default function MetricCard({
  label,
  value,
  hint,
  accent = "#0f766e",
  labelFontSize = 13,
  valueFontSize = 36,
  hintFontSize = 15,
}: MetricCardProps) {
  return (
    <article
      style={{
        padding: 18,
        borderRadius: 22,
        border: "1px solid rgba(148, 163, 184, 0.14)",
        background:
          "linear-gradient(180deg, rgba(11,23,35,0.92), rgba(15,23,42,0.88))",
        boxShadow: "0 18px 40px rgba(2, 6, 23, 0.28)",
      }}
    >
      <div
        style={{
          display: "inline-block",
          marginBottom: 12,
          padding: "5px 10px",
          borderRadius: 999,
          background: `${accent}22`,
          color: accent,
          fontSize: labelFontSize,
          fontWeight: 700,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
          letterSpacing: "0.04em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      <div style={{ marginBottom: 10, fontSize: valueFontSize, fontWeight: 700, color: "#f8fafc" }}>
        {value}
      </div>
      <p
        style={{
          margin: 0,
          color: "rgba(203, 213, 225, 0.74)",
          lineHeight: 1.6,
          fontSize: hintFontSize,
          fontFamily: "\"Avenir Next\", \"Segoe UI\", \"Helvetica Neue\", sans-serif",
        }}
      >
        {hint}
      </p>
    </article>
  );
}
