import * as Tooltip from "radix-ui/tooltip";

import styles from "./MetricCard.module.css";

interface MetricCardProps {
  label: string;
  value: string;
  hint: string;
  accent?: string;
  labelFontSize?: number;
  valueFontSize?: number;
  hintFontSize?: number;
  density?: "compact" | "comfortable";
}

export default function MetricCard({
  label,
  value,
  hint,
  accent = "#0f766e",
  labelFontSize = 12,
  valueFontSize = 30,
  hintFontSize = 13,
  density = "compact",
}: MetricCardProps) {
  return (
    <article className={styles.card}>
      <div className={density === "compact" ? styles.compact : styles.comfortable}>
        <div className={styles.labelRow}>
          <div className={styles.label} style={{ background: `${accent}22`, color: accent, fontSize: labelFontSize }}>{label}</div>
          {density === "compact" ? (
            <Tooltip.Root delayDuration={250}>
              <Tooltip.Trigger asChild><button type="button" className={styles.helpButton} aria-label={`${label}: ${hint}`}>?</button></Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content className="workspace-tooltip" sideOffset={7}>{hint}</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>
          ) : null}
        </div>
        <div className={styles.value} style={{ fontSize: valueFontSize }}>{value}</div>
        <p className={styles.hint} style={{ fontSize: hintFontSize }}>{hint}</p>
      </div>
    </article>
  );
}
