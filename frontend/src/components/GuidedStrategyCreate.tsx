import type { CSSProperties } from "react";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useMemo, useState } from "react";

import {
  createStrategy,
  getStrategyCatalog,
  getStrategyFeatureSupport,
  validateStrategy,
} from "@/api/strategies";
import Badge from "@/components/Badge";
import { SelectControl } from "@/components/workspace/SelectControl";
import { useI18n } from "@/i18n/provider";
import type {
  StrategyCatalogItem,
  StrategyCreate,
  StrategyFeatureSupport,
  StrategyType,
  StrategyValidation,
} from "@/types/strategy";
import {
  cloneRecord,
  ENGINE_READY_TYPES,
  getPathValue,
  normalizeSymbols,
  setPathValue,
  STRATEGY_GUIDANCE,
  type GuidedFieldDefinition,
} from "@/utils/strategyCreateGuidance";

type CreationView = "hub" | "manual";
type FieldErrors = Record<string, string>;

const STEP_COUNT = 5;

function interpolate(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce(
    (current, [key, value]) => current.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

function fieldId(path: string): string {
  return `strategy-create-${path.replaceAll(".", "-")}`;
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function formatValue(value: unknown, field?: GuidedFieldDefinition): string {
  if (field?.kind === "percent" && typeof value === "number") return `${Number((value * 100).toFixed(4))}%`;
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export default function GuidedStrategyCreate() {
  const router = useRouter();
  const { locale, messages } = useI18n();
  const copy = messages.strategyCreate;
  const isZh = locale === "zh-CN";
  const fieldCopy = copy.fields as Record<string, { label: string; hint: string }>;
  const typeCopy = copy.types as Record<StrategyType, { title: string; summary: string; suitable: string; data: string }>;

  const [view, setView] = useState<CreationView>("hub");
  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [featureSupport, setFeatureSupport] = useState<StrategyFeatureSupport | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<StrategyType | null>(null);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [initialParams, setInitialParams] = useState<Record<string, unknown>>({});
  const [rawJson, setRawJson] = useState("{}");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [symbolsText, setSymbolsText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [showAdvancedSignal, setShowAdvancedSignal] = useState(false);
  const [showAdvancedRisk, setShowAdvancedRisk] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [validation, setValidation] = useState<StrategyValidation | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadCatalog = async () => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const [catalogItems, support] = await Promise.all([
        getStrategyCatalog(),
        getStrategyFeatureSupport(),
      ]);
      setCatalog(catalogItems);
      setFeatureSupport(support);
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : copy.catalog.failed);
    } finally {
      setCatalogLoading(false);
    }
  };

  useEffect(() => {
    void loadCatalog();
    // The loader is intentionally stable for this one-time request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedCatalog = useMemo(
    () => catalog.find((item) => item.strategy_type === selectedType) ?? null,
    [catalog, selectedType],
  );
  const symbols = useMemo(() => normalizeSymbols(symbolsText), [symbolsText]);

  const invalidate = () => {
    setDirty(true);
    setValidation(null);
    setValidationError(null);
  };

  const chooseType = (strategyType: StrategyType) => {
    if (strategyType === selectedType) return;
    if (selectedType && dirty && typeof window !== "undefined" && !window.confirm(copy.wizard.changeWarning)) return;
    const template = catalog.find((item) => item.strategy_type === strategyType);
    if (!template) return;
    const defaults = cloneRecord(template.defaults);
    setSelectedType(strategyType);
    setParams(defaults);
    setInitialParams(cloneRecord(defaults));
    setRawJson(JSON.stringify(defaults, null, 2));
    setErrors({});
    setValidation(null);
    setValidationError(null);
    setDirty(false);
    setShowAdvancedSignal(false);
    setShowAdvancedRisk(false);
  };

  const updateField = (field: GuidedFieldDefinition, value: unknown) => {
    setParams((current) => {
      let next = setPathValue(current, field.path, value);
      if (field.path === "signal.fast_indicator.kind" || field.path === "signal.slow_indicator.kind") {
        const prefix = field.path.includes("fast_indicator") ? "fast_indicator" : "slow_indicator";
        const available = value === "ema"
          ? featureSupport?.trend.ema_windows ?? []
          : featureSupport?.trend.sma_windows ?? [];
        const windowPath = `signal.${prefix}.window`;
        if (available.length > 0 && !available.includes(Number(getPathValue(next, windowPath)))) {
          next = setPathValue(next, windowPath, available[0]);
        }
      }
      return next;
    });
    setErrors((current) => {
      const next = { ...current };
      delete next[field.path];
      return next;
    });
    invalidate();
  };

  const buildParams = (): Record<string, unknown> => {
    let result: Record<string, unknown>;
    if (selectedType === "custom") {
      result = safeRecord(JSON.parse(rawJson));
    } else {
      result = cloneRecord(params);
    }
    result = setPathValue(result, "universe.symbols", symbols);
    result = setPathValue(result, "universe.selection_mode", symbols.length ? "manual" : "all_common_stock");
    result = setPathValue(result, "metadata.description", description.trim());
    result = setPathValue(result, "metadata.schema_version", Number(getPathValue(result, "metadata.schema_version")) || 1);
    return result;
  };

  const buildPayload = (): StrategyCreate => {
    if (!selectedType) throw new Error(copy.errors.validateFailed);
    return {
      name: name.trim(),
      description: description.trim(),
      strategy_type: selectedType,
      status: "draft",
      params: buildParams(),
    };
  };

  const validateFields = (fields: GuidedFieldDefinition[]): FieldErrors => {
    const next: FieldErrors = {};
    fields.forEach((field) => {
      if (field.kind === "boolean" || field.kind === "select") return;
      const value = Number(getPathValue(params, field.path));
      if (!Number.isFinite(value)) {
        next[field.path] = copy.errors.invalidNumber;
      } else if (field.integer && !Number.isInteger(value)) {
        next[field.path] = copy.errors.integerRequired;
      } else if (field.min !== undefined && value < field.min) {
        next[field.path] = interpolate(copy.errors.belowMinimum, { value: field.kind === "percent" ? `${field.min * 100}%` : field.min });
      } else if (field.max !== undefined && value > field.max) {
        next[field.path] = interpolate(copy.errors.aboveMaximum, { value: field.kind === "percent" ? `${field.max * 100}%` : field.max });
      }
    });
    return next;
  };

  const validateCurrentStep = (): boolean => {
    let next: FieldErrors = {};
    if (step === 0 && !selectedType) next.strategyType = copy.errors.typeRequired;
    if (step === 1) {
      if (!name.trim()) next.name = copy.errors.nameRequired;
      if (description.length > 500) next.description = copy.errors.descriptionTooLong;
    }
    if (step === 2) {
      if (selectedType === "custom") {
        try {
          safeRecord(JSON.parse(rawJson));
        } catch {
          next.rawJson = copy.custom.invalid;
        }
      } else if (selectedType) {
        next = validateFields(STRATEGY_GUIDANCE[selectedType].signal);
        if (selectedType === "island_reversal" && Number(getPathValue(params, "signal.max_island_bars")) < Number(getPathValue(params, "signal.min_island_bars"))) {
          next["signal.max_island_bars"] = copy.errors.islandRange;
        }
        if (selectedType === "double_bottom" && Number(getPathValue(params, "signal.max_bottom_spacing")) < Number(getPathValue(params, "signal.min_bottom_spacing"))) {
          next["signal.max_bottom_spacing"] = copy.errors.bottomSpacing;
        }
        if (selectedType === "support_resistance") {
          const modes = ["support_bounce_enabled", "resistance_breakout_enabled", "breakout_retest_enabled"];
          if (!modes.some((key) => Boolean(getPathValue(params, `signal.${key}`)))) next["signal.support_bounce_enabled"] = copy.errors.supportMode;
          const covered = Number(getPathValue(params, "signal.pivot_left_bars")) + Number(getPathValue(params, "signal.pivot_right_bars")) + 1;
          if (Number(getPathValue(params, "signal.detection_window")) < covered) next["signal.detection_window"] = copy.errors.detectionWindow;
        }
      }
    }
    if (step === 3 && selectedType && selectedType !== "custom") next = validateFields(STRATEGY_GUIDANCE[selectedType].risk);
    setErrors(next);
    const first = Object.keys(next)[0];
    if (first && typeof document !== "undefined") {
      window.setTimeout(() => document.getElementById(fieldId(first))?.focus(), 0);
    }
    return Object.keys(next).length === 0;
  };

  const nextStep = () => {
    if (!validateCurrentStep()) return;
    setStep((current) => Math.min(STEP_COUNT - 1, current + 1));
  };

  const runValidation = async () => {
    setValidation(null);
    setValidationError(null);
    setValidating(true);
    try {
      const payload = buildPayload();
      const data = selectedType === "custom"
        ? {
            valid: true,
            engine_ready: false,
            strategy_type: "custom" as const,
            normalized_params: payload.params,
          }
        : await validateStrategy(payload);
      setValidation(data);
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : copy.errors.validateFailed);
    } finally {
      setValidating(false);
    }
  };

  const save = async () => {
    if (!validation || saving) return;
    setSaving(true);
    setValidationError(null);
    try {
      const created = await createStrategy(
        buildPayload(),
        (globalThis.crypto as Crypto | undefined)?.randomUUID?.() ?? String(Date.now()),
      );
      await router.push(`/strategies/${encodeURIComponent(created.id)}?created=1`);
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : copy.errors.createFailed);
      setSaving(false);
    }
  };

  const stepLabels = [
    copy.wizard.stepType,
    copy.wizard.stepBasics,
    copy.wizard.stepSignal,
    copy.wizard.stepRisk,
    copy.wizard.stepReview,
  ];

  const renderHub = () => (
    <div style={{ display: "grid", gap: 18 }}>
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.hub.title}</h2>
        <p style={sectionSubtitleStyle}>{copy.hub.subtitle}</p>
        <div style={pathGridStyle}>
          <article style={{ ...pathCardStyle, borderColor: "rgba(8,145,178,.65)" }}>
            <div style={pathIconStyle}>01</div>
            <h3 style={cardTitleStyle}>{copy.hub.manualTitle}</h3>
            <p style={cardTextStyle}>{copy.hub.manualDescription}</p>
            <button type="button" onClick={() => setView("manual")} style={primaryButtonStyle}>
              {copy.hub.manualAction}
            </button>
          </article>
          <article style={pathCardStyle}>
            <div style={pathIconStyle}>02</div>
            <h3 style={cardTitleStyle}>{copy.hub.agentTitle}</h3>
            <p style={cardTextStyle}>{copy.hub.agentDescription}</p>
            <div style={{ display: "grid", gap: 10, marginTop: 16 }}>
              <AgentLink
                href="/research?mode=category&source=strategy-create"
                title={copy.hub.categoryTitle}
                description={copy.hub.categoryDescription}
                action={copy.hub.categoryAction}
              />
              <AgentLink
                href="/research?mode=algorithm&source=strategy-create"
                title={copy.hub.algorithmTitle}
                description={copy.hub.algorithmDescription}
                action={copy.hub.algorithmAction}
              />
            </div>
          </article>
        </div>
      </section>
      <div style={safetyStyle}>{copy.hub.safety}</div>
    </div>
  );

  const renderTypeStep = () => {
    if (catalogLoading) return <section style={panelStyle}><p>{copy.catalog.loading}</p></section>;
    if (catalogError) return (
      <section style={panelStyle}>
        <p style={{ color: "#fda4af" }}>{copy.catalog.failed}</p>
        <p style={mutedStyle}>{catalogError}</p>
        <button type="button" onClick={() => void loadCatalog()} style={secondaryButtonStyle}>{copy.catalog.retry}</button>
      </section>
    );
    return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.wizard.stepType}</h2>
        <p style={sectionSubtitleStyle}>{copy.catalog.execution}</p>
        <div style={templateGridStyle}>
          {ENGINE_READY_TYPES.map((strategyType) => {
            const item = catalog.find((candidate) => candidate.strategy_type === strategyType);
            if (!item) return null;
            const guidance = typeCopy[strategyType];
            const selected = selectedType === strategyType;
            return (
              <button
                key={strategyType}
                type="button"
                aria-pressed={selected}
                onClick={() => chooseType(strategyType)}
                style={templateCardStyle(selected)}
              >
                <div style={cardHeaderStyle}>
                  <strong style={{ fontSize: 18 }}>{guidance.title}</strong>
                  <Badge tone="success">{copy.catalog.engineReady}</Badge>
                </div>
                <code style={codeStyle}>{strategyType}</code>
                <p style={cardTextStyle}>{guidance.summary}</p>
                <p style={miniTextStyle}><strong>{isZh ? "适用：" : "Suitable: "}</strong>{guidance.suitable}</p>
                <p style={miniTextStyle}><strong>{isZh ? "数据：" : "Data: "}</strong>{guidance.data}</p>
                {selected ? <span style={selectedPillStyle}>{copy.wizard.selected}</span> : null}
              </button>
            );
          })}
        </div>
        {errors.strategyType ? <p role="alert" style={errorStyle}>{errors.strategyType}</p> : null}
        {catalog.some((item) => item.strategy_type === "custom") ? (
          <details style={{ ...detailsStyle, marginTop: 18 }}>
            <summary style={summaryStyle}>{copy.catalog.customTitle}</summary>
            <p style={cardTextStyle}>{copy.catalog.customDescription}</p>
            <button type="button" onClick={() => chooseType("custom")} style={secondaryButtonStyle}>{copy.catalog.customAction}</button>
          </details>
        ) : null}
      </section>
    );
  };

  const renderBasicsStep = () => (
    <section style={panelStyle}>
      <h2 style={sectionTitleStyle}>{copy.basics.title}</h2>
      <p style={sectionSubtitleStyle}>{copy.basics.subtitle}</p>
      <div style={formGridStyle}>
        <FieldShell label={copy.basics.name} error={errors.name} inputId={fieldId("name")} full>
          <input
            id={fieldId("name")}
            value={name}
            maxLength={128}
            onChange={(event) => { setName(event.target.value); invalidate(); setErrors((current) => ({ ...current, name: "" })); }}
            placeholder={copy.basics.namePlaceholder}
            style={inputStyle(Boolean(errors.name))}
          />
        </FieldShell>
        <FieldShell label={copy.basics.description} error={errors.description} inputId={fieldId("description")} full>
          <textarea
            id={fieldId("description")}
            value={description}
            maxLength={500}
            rows={4}
            onChange={(event) => { setDescription(event.target.value); invalidate(); setErrors((current) => ({ ...current, description: "" })); }}
            placeholder={copy.basics.descriptionPlaceholder}
            style={{ ...inputStyle(Boolean(errors.description)), resize: "vertical" }}
          />
          <span style={counterStyle}>{description.length}/500</span>
        </FieldShell>
        <FieldShell label={copy.basics.universe} hint={copy.basics.universeEmpty} inputId={fieldId("symbols")} full>
          <textarea
            id={fieldId("symbols")}
            value={symbolsText}
            rows={3}
            onChange={(event) => { setSymbolsText(event.target.value); invalidate(); }}
            placeholder={copy.basics.universePlaceholder}
            style={{ ...inputStyle(false), resize: "vertical" }}
          />
          {symbols.length ? <span style={recognizedStyle}>{interpolate(copy.basics.universeCount, { count: symbols.length, symbols: symbols.join(", ") })}</span> : null}
        </FieldShell>
        <div style={{ ...readonlyCardStyle, gridColumn: "1 / -1" }}>
          <div><strong>{copy.basics.status}</strong><div style={{ marginTop: 8 }}><Badge tone="warning">{copy.basics.draft}</Badge></div></div>
          <p style={{ ...mutedStyle, margin: 0 }}>{copy.basics.draftHint}</p>
        </div>
      </div>
    </section>
  );

  const renderFields = (fields: GuidedFieldDefinition[], showAdvanced: boolean) => {
    const visible = fields.filter((field) => !field.advanced || showAdvanced);
    return <div style={fieldGridStyle}>{visible.map((field) => renderField(field))}</div>;
  };

  const renderField = (field: GuidedFieldDefinition) => {
    const copyForField = fieldCopy[field.key];
    const value = getPathValue(params, field.path);
    const defaultValue = getPathValue(initialParams, field.path);
    const error = errors[field.path];
    const options = getOptions(field);
    const range = [field.min !== undefined ? formatValue(field.min, field) : "−∞", field.max !== undefined ? formatValue(field.max, field) : "+∞"].join(" – ");
    const hints = [copyForField?.hint, interpolate(copy.parameters.defaultValue, { value: formatValue(defaultValue, field) })];
    if (field.kind === "number" || field.kind === "percent") hints.push(interpolate(copy.parameters.allowedRange, { range }));

    return (
      <FieldShell key={field.path} label={copyForField?.label ?? field.path} hint={hints.filter(Boolean).join(" · ")} error={error} inputId={fieldId(field.path)}>
        {field.kind === "boolean" ? (
          <label style={switchStyle}>
            <input
              id={fieldId(field.path)}
              type="checkbox"
              checked={Boolean(value)}
              onChange={(event) => updateField(field, event.target.checked)}
            />
            <span>{Boolean(value) ? (isZh ? "已启用" : "Enabled") : (isZh ? "未启用" : "Disabled")}</span>
          </label>
        ) : field.kind === "select" ? (
          <SelectControl
            id={fieldId(field.path)}
            value={String(value ?? "")}
            onChange={(event) => {
              const option = options.find((candidate) => String(candidate.value) === event.target.value);
              updateField(field, option?.value ?? event.target.value);
            }}
            invalid={Boolean(error)}
          >
            {options.map((option) => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
          </SelectControl>
        ) : (
          <div style={{ position: "relative" }}>
            <input
              id={fieldId(field.path)}
              type="number"
              value={field.kind === "percent" ? Number(value ?? 0) * 100 : Number(value ?? 0)}
              min={field.min === undefined ? undefined : field.kind === "percent" ? field.min * 100 : field.min}
              max={field.max === undefined ? undefined : field.kind === "percent" ? field.max * 100 : field.max}
              step={field.step ?? "any"}
              onChange={(event) => updateField(field, field.kind === "percent" ? Number(event.target.value) / 100 : Number(event.target.value))}
              style={{ ...inputStyle(Boolean(error)), paddingRight: field.kind === "percent" ? 38 : 12 }}
            />
            {field.kind === "percent" ? <span style={unitStyle}>{copy.parameters.percentageUnit}</span> : null}
          </div>
        )}
      </FieldShell>
    );
  };

  const getOptions = (field: GuidedFieldDefinition): Array<{ label: string; value: string | number }> => {
    if (!field.dynamicOptions) return field.options ?? [];
    const indicator = field.dynamicOptions === "fast_window" ? "fast_indicator" : "slow_indicator";
    const kind = String(getPathValue(params, `signal.${indicator}.kind`) ?? "ema");
    const windows = kind === "ema" ? featureSupport?.trend.ema_windows ?? [] : featureSupport?.trend.sma_windows ?? [];
    return windows.map((window) => ({ label: String(window), value: window }));
  };

  const renderSignalStep = () => {
    if (selectedType === "custom") return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.custom.title}</h2>
        <p style={sectionSubtitleStyle}>{copy.custom.subtitle}</p>
        <FieldShell label={copy.custom.label} error={errors.rawJson} inputId={fieldId("rawJson")} full>
          <textarea
            id={fieldId("rawJson")}
            value={rawJson}
            rows={24}
            spellCheck={false}
            onChange={(event) => { setRawJson(event.target.value); invalidate(); setErrors((current) => ({ ...current, rawJson: "" })); }}
            style={{ ...inputStyle(Boolean(errors.rawJson)), resize: "vertical", fontFamily: "SFMono-Regular, Consolas, monospace", fontSize: 12 }}
          />
        </FieldShell>
      </section>
    );
    if (!selectedType) return null;
    const fields = STRATEGY_GUIDANCE[selectedType].signal;
    const hasAdvanced = fields.some((field) => field.advanced);
    return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.parameters.signalTitle}</h2>
        <p style={sectionSubtitleStyle}>{copy.parameters.signalSubtitle}</p>
        <h3 style={groupTitleStyle}>{copy.parameters.core}</h3>
        {renderFields(fields.filter((field) => !field.advanced), true)}
        {hasAdvanced ? (
          <details open={showAdvancedSignal} onToggle={(event) => setShowAdvancedSignal((event.currentTarget as HTMLDetailsElement).open)} style={detailsStyle}>
            <summary style={summaryStyle}>{showAdvancedSignal ? copy.parameters.hideAdvanced : copy.parameters.showAdvanced}</summary>
            <div style={{ marginTop: 16 }}>{renderFields(fields.filter((field) => field.advanced), true)}</div>
          </details>
        ) : null}
      </section>
    );
  };

  const renderRiskStep = () => {
    if (selectedType === "custom") return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.parameters.riskTitle}</h2>
        <p style={sectionSubtitleStyle}>{copy.custom.subtitle}</p>
        <ExecutionSummary copy={copy.parameters} params={safeRecord(JSON.parse(rawJson || "{}"))} />
      </section>
    );
    if (!selectedType) return null;
    const fields = STRATEGY_GUIDANCE[selectedType].risk;
    const hasAdvanced = fields.some((field) => field.advanced);
    return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.parameters.riskTitle}</h2>
        <p style={sectionSubtitleStyle}>{copy.parameters.riskSubtitle}</p>
        <h3 style={groupTitleStyle}>{copy.parameters.core}</h3>
        {renderFields(fields.filter((field) => !field.advanced), true)}
        {hasAdvanced ? (
          <details open={showAdvancedRisk} onToggle={(event) => setShowAdvancedRisk((event.currentTarget as HTMLDetailsElement).open)} style={detailsStyle}>
            <summary style={summaryStyle}>{showAdvancedRisk ? copy.parameters.hideAdvanced : copy.parameters.showAdvanced}</summary>
            <div style={{ marginTop: 16 }}>{renderFields(fields.filter((field) => field.advanced), true)}</div>
          </details>
        ) : null}
        <ExecutionSummary copy={copy.parameters} params={params} />
      </section>
    );
  };

  const renderReviewStep = () => {
    const payload = (() => { try { return buildPayload(); } catch { return null; } })();
    return (
      <section style={panelStyle}>
        <h2 style={sectionTitleStyle}>{copy.review.title}</h2>
        <p style={sectionSubtitleStyle}>{copy.review.subtitle}</p>
        <div style={reviewGridStyle}>
          <ReviewItem label={copy.review.type} value={selectedType ? `${typeCopy[selectedType].title} · ${selectedType}` : "—"} />
          <ReviewItem label={copy.review.name} value={name || "—"} />
          <ReviewItem label={copy.review.status} value="Draft" />
          <ReviewItem label={copy.review.universe} value={symbols.length ? interpolate(copy.review.symbols, { count: symbols.length }) : copy.review.allCommonStock} />
          <ReviewItem label={copy.review.execution} value={copy.review.executionValue} wide />
        </div>
        <div style={{ ...validationStyle, borderColor: validation ? "rgba(34,197,94,.52)" : validationError ? "rgba(244,63,94,.5)" : "rgba(14,165,233,.4)" }}>
          <strong>{validation ? selectedType === "custom" ? copy.review.customValidationPassed : copy.review.validationPassed : validationError ? copy.review.validationFailed : copy.review.validationPending}</strong>
          {validationError ? <p style={{ margin: "8px 0 0", color: "#fda4af" }}>{validationError}</p> : null}
        </div>
        <div style={reviewActionsStyle}>
          <button type="button" disabled={validating || saving} onClick={() => void runValidation()} style={secondaryButtonStyle}>
            {validating ? copy.wizard.validating : copy.wizard.validate}
          </button>
          <button type="button" disabled={!validation || saving || validating} onClick={() => void save()} style={primaryButtonStyle}>
            {saving ? copy.wizard.saving : copy.wizard.save}
          </button>
        </div>
        {payload ? <details style={detailsStyle}><summary style={summaryStyle}>{copy.review.payload}</summary><pre style={jsonStyle}>{JSON.stringify(payload, null, 2)}</pre></details> : null}
        {validation ? <details style={detailsStyle}><summary style={summaryStyle}>{copy.review.normalized}</summary><pre style={jsonStyle}>{JSON.stringify(validation.normalized_params, null, 2)}</pre></details> : null}
      </section>
    );
  };

  const renderManual = () => (
    <div>
      <div style={wizardTopStyle}>
        <button type="button" onClick={() => { setView("hub"); setStep(0); }} style={textButtonStyle}>{copy.wizard.exit}</button>
        <span style={mutedStyle}>{selectedCatalog ? `${typeCopy[selectedCatalog.strategy_type].title} · ${selectedCatalog.strategy_type}` : copy.wizard.stepType}</span>
      </div>
      <ol aria-label={isZh ? "创建策略步骤" : "Strategy creation steps"} style={stepperStyle}>
        {stepLabels.map((label, index) => (
          <li key={label} aria-current={index === step ? "step" : undefined} style={stepStyle(index, step)}>
            <span style={stepNumberStyle(index, step)}>{index + 1}</span><span>{label}</span>
          </li>
        ))}
      </ol>
      {step === 0 ? renderTypeStep() : step === 1 ? renderBasicsStep() : step === 2 ? renderSignalStep() : step === 3 ? renderRiskStep() : renderReviewStep()}
      <div style={footerStyle}>
        <button type="button" disabled={step === 0 || saving || validating} onClick={() => { setErrors({}); setStep((current) => Math.max(0, current - 1)); }} style={secondaryButtonStyle}>{copy.wizard.back}</button>
        {step < STEP_COUNT - 1 ? <button type="button" disabled={catalogLoading || Boolean(catalogError)} onClick={nextStep} style={primaryButtonStyle}>{copy.wizard.next}</button> : null}
      </div>
    </div>
  );

  return view === "hub" ? renderHub() : renderManual();
}

function AgentLink({ href, title, description, action }: { href: string; title: string; description: string; action: string }) {
  return (
    <Link href={href} style={agentLinkStyle}>
      <span><strong>{title}</strong><small style={{ display: "block", marginTop: 5, color: "#94a3b8", lineHeight: 1.45 }}>{description}</small></span>
      <span style={{ marginLeft: "auto", color: "#67e8f9", fontWeight: 800, textAlign: "right" }}>{action} →</span>
    </Link>
  );
}

function FieldShell({ label, hint, error, inputId, full = false, children }: { label: string; hint?: string; error?: string; inputId: string; full?: boolean; children: React.ReactNode }) {
  return (
    <div style={{ ...fieldShellStyle, gridColumn: full ? "1 / -1" : undefined }}>
      <label htmlFor={inputId} style={labelStyle}>{label}</label>
      {children}
      {error ? <span role="alert" style={errorStyle}>{error}</span> : hint ? <span style={hintStyle}>{hint}</span> : null}
    </div>
  );
}

function ExecutionSummary({ copy, params }: { copy: Record<string, string>; params: Record<string, unknown> }) {
  const rebalance = String(getPathValue(params, "execution.rebalance") ?? "daily");
  return (
    <div style={{ marginTop: 20 }}>
      <h3 style={groupTitleStyle}>{copy.executionTitle}</h3>
      <div style={executionGridStyle}>
        <ReviewItem label={copy.timeframe} value={copy.timeframeValue} />
        <ReviewItem label={copy.signalTiming} value={copy.signalTimingValue} />
        <ReviewItem label={copy.fillTiming} value={copy.fillTimingValue} />
        <ReviewItem label={copy.rebalance} value={interpolate(copy.rebalanceValue, { value: rebalance })} />
      </div>
    </div>
  );
}

function ReviewItem({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return <div style={{ ...reviewItemStyle, gridColumn: wide ? "1 / -1" : undefined }}><span style={hintStyle}>{label}</span><strong>{value}</strong></div>;
}

const font = '"Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif';
const panelStyle: CSSProperties = { padding: 22, borderRadius: 24, border: "1px solid rgba(71,85,105,.32)", background: "linear-gradient(180deg,rgba(8,15,24,.94),rgba(15,23,42,.88))", color: "#e2e8f0", boxShadow: "0 18px 44px rgba(2,6,23,.2)" };
const sectionTitleStyle: CSSProperties = { margin: "0 0 8px", fontSize: 26 };
const sectionSubtitleStyle: CSSProperties = { margin: "0 0 20px", color: "#94a3b8", lineHeight: 1.65, fontFamily: font };
const pathGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: 16 };
const pathCardStyle: CSSProperties = { padding: 20, borderRadius: 20, border: "1px solid rgba(71,85,105,.38)", background: "rgba(2,6,23,.46)" };
const pathIconStyle: CSSProperties = { display: "grid", placeItems: "center", width: 38, height: 38, marginBottom: 14, borderRadius: 12, color: "#a5f3fc", background: "rgba(8,145,178,.18)", fontWeight: 900, fontFamily: font };
const cardTitleStyle: CSSProperties = { margin: 0, fontSize: 20 };
const cardTextStyle: CSSProperties = { color: "#a8b4c5", lineHeight: 1.6, fontFamily: font };
const miniTextStyle: CSSProperties = { margin: "7px 0", color: "#94a3b8", fontSize: 13, lineHeight: 1.45, fontFamily: font };
const primaryButtonStyle: CSSProperties = { padding: "11px 16px", border: 0, borderRadius: 12, background: "linear-gradient(135deg,#0891b2,#0e7490)", color: "white", fontWeight: 800, cursor: "pointer", fontFamily: font };
const secondaryButtonStyle: CSSProperties = { padding: "10px 15px", border: "1px solid rgba(100,116,139,.46)", borderRadius: 12, background: "rgba(15,23,42,.8)", color: "#e2e8f0", fontWeight: 750, cursor: "pointer", fontFamily: font };
const textButtonStyle: CSSProperties = { padding: 0, border: 0, background: "transparent", color: "#67e8f9", cursor: "pointer", fontWeight: 750, fontFamily: font };
const safetyStyle: CSSProperties = { padding: "14px 18px", borderRadius: 16, border: "1px solid rgba(34,197,94,.28)", color: "#bbf7d0", background: "rgba(22,101,52,.12)", lineHeight: 1.55, fontFamily: font };
const agentLinkStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, padding: 14, borderRadius: 14, border: "1px solid rgba(71,85,105,.32)", color: "#e2e8f0", background: "rgba(15,23,42,.66)", textDecoration: "none", fontFamily: font };
const wizardTopStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" };
const stepperStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(5,minmax(130px,1fr))", gap: 8, padding: 0, margin: "0 0 18px", listStyle: "none", overflowX: "auto" };
const stepStyle = (index: number, current: number): CSSProperties => ({ display: "flex", alignItems: "center", gap: 8, minWidth: 130, padding: "11px 12px", borderRadius: 13, border: `1px solid ${index === current ? "rgba(34,211,238,.58)" : "rgba(71,85,105,.3)"}`, color: index <= current ? "#e2e8f0" : "#64748b", background: index === current ? "rgba(8,145,178,.15)" : "rgba(2,6,23,.28)", fontSize: 13, fontWeight: 750, fontFamily: font });
const stepNumberStyle = (index: number, current: number): CSSProperties => ({ display: "grid", placeItems: "center", width: 24, height: 24, flex: "0 0 auto", borderRadius: 999, color: index <= current ? "#ecfeff" : "#64748b", background: index <= current ? "#0e7490" : "#1e293b" });
const templateGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))", gap: 13 };
const templateCardStyle = (selected: boolean): CSSProperties => ({ position: "relative", padding: 17, textAlign: "left", borderRadius: 17, border: `1px solid ${selected ? "#22d3ee" : "rgba(71,85,105,.34)"}`, color: "#e2e8f0", background: selected ? "rgba(8,145,178,.14)" : "rgba(2,6,23,.4)", cursor: "pointer", fontFamily: font });
const cardHeaderStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 };
const codeStyle: CSSProperties = { display: "inline-block", marginTop: 8, color: "#67e8f9", fontSize: 12 };
const selectedPillStyle: CSSProperties = { display: "inline-block", marginTop: 10, padding: "5px 9px", borderRadius: 999, color: "#cffafe", background: "rgba(8,145,178,.35)", fontSize: 12, fontWeight: 800 };
const detailsStyle: CSSProperties = { padding: 15, borderRadius: 15, border: "1px solid rgba(71,85,105,.32)", background: "rgba(2,6,23,.32)" };
const summaryStyle: CSSProperties = { cursor: "pointer", color: "#bae6fd", fontWeight: 800, fontFamily: font };
const formGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))", gap: 16 };
const fieldGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 14 };
const fieldShellStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 7 };
const labelStyle: CSSProperties = { color: "#e2e8f0", fontWeight: 750, fontFamily: font };
const inputStyle = (error: boolean): CSSProperties => ({ boxSizing: "border-box", width: "100%", padding: 12, border: `1px solid ${error ? "#fb7185" : "rgba(71,85,105,.58)"}`, borderRadius: 12, color: "#e2e8f0", background: "#07111c", font: "inherit", lineHeight: 1.4 });
const hintStyle: CSSProperties = { color: "#8291a5", fontSize: 12, lineHeight: 1.5, fontFamily: font };
const errorStyle: CSSProperties = { color: "#fda4af", fontSize: 12, lineHeight: 1.5, fontFamily: font };
const counterStyle: CSSProperties = { alignSelf: "flex-end", color: "#64748b", fontSize: 12 };
const recognizedStyle: CSSProperties = { color: "#a5f3fc", fontSize: 12, lineHeight: 1.5 };
const readonlyCardStyle: CSSProperties = { display: "grid", gridTemplateColumns: "minmax(140px,.4fr) minmax(240px,1fr)", gap: 18, alignItems: "center", padding: 16, borderRadius: 14, border: "1px solid rgba(245,158,11,.28)", background: "rgba(120,53,15,.1)" };
const mutedStyle: CSSProperties = { color: "#94a3b8", fontFamily: font };
const switchStyle: CSSProperties = { display: "flex", alignItems: "center", gap: 9, minHeight: 44, padding: "0 12px", borderRadius: 12, border: "1px solid rgba(71,85,105,.5)", background: "#07111c", cursor: "pointer", fontFamily: font };
const unitStyle: CSSProperties = { position: "absolute", top: "50%", right: 13, transform: "translateY(-50%)", color: "#67e8f9", pointerEvents: "none", fontWeight: 800 };
const groupTitleStyle: CSSProperties = { margin: "20px 0 12px", fontSize: 15, color: "#bae6fd", textTransform: "uppercase", letterSpacing: ".06em" };
const executionGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))", gap: 10 };
const reviewGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 10 };
const reviewItemStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 6, padding: 14, borderRadius: 13, border: "1px solid rgba(71,85,105,.3)", background: "rgba(2,6,23,.34)", fontFamily: font };
const validationStyle: CSSProperties = { marginTop: 18, padding: 15, borderRadius: 14, border: "1px solid", background: "rgba(15,23,42,.6)", lineHeight: 1.55, fontFamily: font };
const reviewActionsStyle: CSSProperties = { display: "flex", justifyContent: "flex-end", gap: 10, margin: "16px 0", flexWrap: "wrap" };
const jsonStyle: CSSProperties = { maxHeight: 360, overflow: "auto", marginBottom: 0, padding: 14, borderRadius: 12, background: "#020617", color: "#bae6fd", fontSize: 11, lineHeight: 1.5 };
const footerStyle: CSSProperties = { display: "flex", justifyContent: "space-between", gap: 12, marginTop: 16 };
