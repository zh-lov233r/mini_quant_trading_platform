import { useI18n } from "@/i18n/provider";
import type { StrategyType } from "@/types/strategy";
import { getPathValue, setPathValue, STRATEGY_GUIDANCE } from "@/utils/strategyCreateGuidance";

export const BOTTOM_REVERSAL_TYPES = ["island_reversal", "double_bottom", "head_shoulders_bottom", "rounded_bottom", "v_reversal"] as const;
type BottomType = typeof BOTTOM_REVERSAL_TYPES[number];
export function isBottomReversal(type: StrategyType): type is BottomType {
  return BOTTOM_REVERSAL_TYPES.some((item) => item === type);
}

// The two original forms own their basic controls. These are the additional controls they need.
const supplementalFields: Partial<Record<BottomType, string[]>> = {
  island_reversal: ["previous_body_atr_min", "breakout_body_atr_min", "exhaustion_body_atr_max", "island_body_atr_max"],
  double_bottom: ["rebound_volume_ratio_min", "rebound_volume_ratio_max"],
};

export default function BottomReversalFields({ type, params, onChange }: {
  type: BottomType;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>) => void;
}) {
  const { locale, messages } = useI18n();
  const copy = messages.strategyCreate;
  const supplemental = supplementalFields[type];
  const fields = [...STRATEGY_GUIDANCE[type].signal, ...STRATEGY_GUIDANCE[type].risk]
    .filter((field) => field.path !== "signal.min_strength_score" && (!supplemental
      || supplemental.includes(field.path.replace("signal.", "")) || field.path.startsWith("risk.stage_")));
  const errors: Record<string, string> = {};
  for (const [minimum, maximum, message] of [
    ["signal.rebound_volume_ratio_min", "signal.rebound_volume_ratio_max", copy.errors.reboundVolumeRange],
    ["signal.vertex_position_min", "signal.vertex_position_max", copy.errors.vertexRange],
    ["signal.min_segment_bars", "signal.max_segment_bars", copy.errors.segmentRange],
    ["signal.min_lookback", "signal.max_lookback", copy.errors.lookbackRange],
    ["signal.consolidation_min_bars", "signal.consolidation_max_bars", copy.errors.consolidationRange],
  ]) {
    if (Number(getPathValue(params, minimum)) > Number(getPathValue(params, maximum))) errors[maximum] = message;
  }
  const targets = [1, 2, 3].map((stage) => Number(getPathValue(params, `risk.stage_${stage}_target_pct`)));
  if (!(targets[0] < targets[1] && targets[1] < targets[2] && targets[2] === 1)) errors["risk.stage_3_target_pct"] = copy.errors.stageTargets;
  return <fieldset style={{ border: "1px solid #334155", borderRadius: 12, padding: 18 }}>
    <legend>{locale === "zh-CN" ? "底部反转量化参数" : "Bottom reversal parameters"}</legend>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
      {fields.map((field) => {
        const text = copy.fields[field.key as keyof typeof copy.fields];
        const scale = field.kind === "percent" ? 100 : 1;
        const value = getPathValue(params, field.path);
        const error = errors[field.path];
        const id = `bottom-${field.path}`;
        return <div key={field.path} style={{ display: "grid", gap: 6, alignContent: "start" }}>
          <label htmlFor={id}>{text.label}{scale === 100 ? " (%)" : ""}</label>
          <input id={id} type="number" required value={typeof value === "number" ? value * scale : ""}
            min={field.min == null ? undefined : field.min * scale} max={field.max == null ? undefined : field.max * scale}
            step={field.integer ? 1 : "any"} aria-invalid={Boolean(error)} aria-describedby={`${id}-help`}
            ref={(input) => { input?.setCustomValidity(error || ""); }}
            onChange={(event) => onChange(setPathValue(params, field.path, event.target.value === "" ? "" : Number(event.target.value) / scale))}
            style={{ width: "100%", minWidth: 0, padding: 10, color: "inherit", background: "transparent", border: `1px solid ${error ? "#f87171" : "#475569"}`, borderRadius: 8 }} />
          <small id={`${id}-help`} style={{ color: error ? "#f87171" : "#94a3b8" }}>{error || text.hint}</small>
        </div>;
      })}
    </div>
  </fieldset>;
}
