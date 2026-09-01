import { Select } from "radix-ui";
import type { CSSProperties, ReactNode } from "react";

import styles from "./SelectControl.module.css";
import {
  normalizeSelectControlValue,
  type SelectControlValue,
} from "./selectControlUtils";

export type { SelectControlValue } from "./selectControlUtils";

export interface SelectControlOption {
  value: SelectControlValue;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  accent?: string;
}

export interface SelectControlProps {
  value: SelectControlValue;
  onValueChange: (value: string) => void;
  options: readonly SelectControlOption[];
  density?: "default" | "compact";
  invalid?: boolean;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  className?: string;
  placeholder?: string;
  style?: CSSProperties;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
}

export function SelectControl({
  value,
  onValueChange,
  options,
  density = "default",
  invalid = false,
  disabled = false,
  required = false,
  name,
  id,
  className,
  placeholder,
  style,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  "aria-describedby": ariaDescribedBy,
}: SelectControlProps) {
  const normalizedValue = normalizeSelectControlValue(value);
  const selectedOption = options.find(
    (option) => normalizeSelectControlValue(option.value) === normalizedValue,
  );

  return (
    <Select.Root
      value={normalizedValue}
      onValueChange={onValueChange}
      disabled={disabled || options.length === 0}
      required={required}
      name={name}
    >
      <Select.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-invalid={invalid || undefined}
        className={`${styles.trigger} ${density === "compact" ? styles.compact : ""} ${className || ""}`}
        style={style}
      >
        <span className={styles.selected}>
          {selectedOption?.accent ? (
            <span className={styles.dot} style={{ background: selectedOption.accent }} aria-hidden="true" />
          ) : null}
          <Select.Value placeholder={placeholder}>{selectedOption?.label}</Select.Value>
        </span>
        <Select.Icon asChild>
          <svg className={styles.chevron} viewBox="0 0 20 20" aria-hidden="true">
            <path d="m6 8 4 4 4-4" />
          </svg>
        </Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content className={styles.content} position="popper" sideOffset={6} collisionPadding={12}>
          <Select.ScrollUpButton className={styles.scrollButton} aria-hidden="true">⌃</Select.ScrollUpButton>
          <Select.Viewport className={styles.viewport}>
            {options.map((option) => (
              <Select.Item
                key={normalizeSelectControlValue(option.value)}
                value={normalizeSelectControlValue(option.value)}
                disabled={option.disabled}
                className={styles.option}
              >
                {option.accent ? (
                  <span className={styles.dot} style={{ background: option.accent }} aria-hidden="true" />
                ) : (
                  <span className={styles.dotPlaceholder} aria-hidden="true" />
                )}
                <Select.ItemText>
                  <span className={styles.optionText}>
                    <span>{option.label}</span>
                    {option.description ? (
                      <span className={styles.optionDescription}>{option.description}</span>
                    ) : null}
                  </span>
                </Select.ItemText>
                <Select.ItemIndicator className={styles.check}>✓</Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
          <Select.ScrollDownButton className={styles.scrollButton} aria-hidden="true">⌄</Select.ScrollDownButton>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
