import type { CSSProperties } from "react";
import { useI18n } from "@/i18n/provider";
import { getStrategyCategoryPresentation, isStrategyType } from "@/utils/strategy";
import styles from "./Signals.module.css";

export function StrategyInstanceLabel({ name, version, type }: { name: string; version?: number; type: string }) {
  const { locale } = useI18n();
  const category = isStrategyType(type) ? getStrategyCategoryPresentation(type, locale) : null;
  return <span className={styles.strategyInstance} style={category ? { "--strategy-accent": category.accent } as CSSProperties : undefined}>
    <i aria-hidden="true"/>
    <span>{name}{version === undefined ? "" : ` · v${version}`}</span>
  </span>;
}
