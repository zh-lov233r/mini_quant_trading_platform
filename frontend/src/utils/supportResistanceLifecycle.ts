import type { BacktestSignalOut, SupportResistanceZoneVersionOut } from "@/types/backtest";

export function lifecycleSignalFeatures(
  signals: BacktestSignalOut[], symbol: string, action: "BUY" | "SELL", timestamp: string | null,
): Record<string, unknown> | null {
  if (!timestamp) return null;
  return signals.find((signal) => signal.symbol.toUpperCase() === symbol.toUpperCase()
    && signal.signal === action && signal.ts != null
    && Date.parse(signal.ts) === Date.parse(timestamp))?.features ?? null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

// Only selected, persisted evidence belongs to a trade; candidates are not selections.
export function lifecycleTradeZoneKeys(
  entryFeatures: Record<string, unknown> | null | undefined,
  exitFeatures: Record<string, unknown> | null | undefined,
): Set<string> {
  const keys = new Set<string>();
  const add = (value: unknown) => {
    if (typeof value === "string" && value.length > 0) keys.add(value);
  };
  for (const features of [entryFeatures, exitFeatures]) {
    const sr = record(features?.support_resistance);
    add(sr?.zone_key);
    add(record(sr?.zone)?.zone_key);
    const channel = record(sr?.entry_channel);
    add(channel?.support_zone_key);
    add(channel?.resistance_zone_key);
  }
  const exit = record(exitFeatures?.support_resistance);
  if (exit?.exit_reason_code === "downtrend") {
    const evidence = record(exit.exit_regime_evidence);
    add(evidence?.lower_zone_key);
    add(evidence?.upper_zone_key);
  }
  return keys;
}

type ZoneBand = Pick<SupportResistanceZoneVersionOut, "id" | "zone_key" | "role" | "atr_width" | "source_metadata"> & {
  geometry: NonNullable<SupportResistanceZoneVersionOut["geometry"]>;
  retrospective: boolean;
};

// Each phase has a unique key and exactly one immutable active definition.
// Historical fitting is an explicit retrospective segment, never an active zone.
export function lifecycleZoneBands(
  versions: SupportResistanceZoneVersionOut[], sessionDates: string[],
  tradeZoneKeys: Set<string>, showAll: boolean,
): ZoneBand[] {
  const dates = new Set(sessionDates);
  return versions.flatMap((version) => {
    if (version.status !== "active" || (!showAll && !tradeZoneKeys.has(version.zone_key))) return [];
    return [
      { geometry: version.formation_geometry, retrospective: true },
      { geometry: version.geometry, retrospective: false },
    ].flatMap(({ geometry, retrospective }) => geometry
      && dates.has(geometry.start_date) && dates.has(geometry.end_date)
      ? [{ id: version.id, zone_key: version.zone_key, role: version.role,
          atr_width: version.atr_width, source_metadata: version.source_metadata,
          geometry, retrospective }] : []);
  });
}
