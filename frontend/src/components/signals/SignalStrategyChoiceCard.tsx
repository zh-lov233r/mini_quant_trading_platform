import type { CSSProperties } from "react";
import type { StrategyOut } from "@/types/strategy";
import { getStrategyCategoryPresentation } from "@/utils/strategy";
import card from "@/components/strategies/StrategyCard.module.css";

export function SignalStrategyChoiceCard({
  strategy,
  locale,
  selected,
  supported,
  supportedLabel,
  unsupportedLabel,
  onChange,
}: {
  strategy: StrategyOut;
  locale: "zh-CN" | "en-US";
  selected: boolean;
  supported: boolean;
  supportedLabel: string;
  unsupportedLabel: string;
  onChange: (selected: boolean) => void;
}) {
  const category = getStrategyCategoryPresentation(strategy.strategy_type, locale);
  const style = {
    "--strategy-card-accent": category.accent,
    "--strategy-card-accent-rgb": category.accentRgb,
    "--workspace-card-accent": category.accentRgb,
  } as CSSProperties;

  return (
    <label
      className={`${card.card} ${card.compact} ${selected ? card.selected : ""} ${supported ? "" : card.disabled}`}
      style={style}
    >
      <span className={card.categoryRow}>
        <span className={card.category}>
          <span className={card.dot} aria-hidden="true" />
          {category.label}
        </span>
        <span className={card.technical}>{strategy.strategy_type} · v{strategy.version}</span>
      </span>
      <strong className={card.title}>{strategy.name}</strong>
      <span className={card.status}>{supported ? supportedLabel : unsupportedLabel}</span>
      <input
        className={card.checkbox}
        type="checkbox"
        aria-label={`${strategy.name} · ${category.label}`}
        checked={selected}
        disabled={!supported}
        onChange={event => onChange(event.target.checked)}
      />
    </label>
  );
}
