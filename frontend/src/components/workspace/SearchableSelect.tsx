import * as Popover from "radix-ui/popover";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import {
  edgeEnabledOptionIndex,
  enabledOptionIndex,
  filterSearchableOptions,
  sortSearchableOptions,
  type SearchableSelectOption,
} from "./searchableSelectUtils";
import styles from "./SearchableSelect.module.css";

export type { SearchableSelectOption } from "./searchableSelectUtils";

export interface SearchableSelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: readonly SearchableSelectOption[];
  ariaLabel: string;
  placeholder: string;
  searchPlaceholder: string;
  emptyText: string;
  clearSearchLabel?: string;
  disabled?: boolean;
  invalid?: boolean;
  sortOptions?: boolean;
}

export function SearchableSelect({
  value,
  onValueChange,
  options,
  ariaLabel,
  placeholder,
  searchPlaceholder,
  emptyText,
  clearSearchLabel = "Clear search",
  disabled = false,
  invalid = false,
  sortOptions = true,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const searchRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const orderedOptions = useMemo(
    () => sortOptions ? sortSearchableOptions(options) : [...options],
    [options, sortOptions],
  );
  const filteredOptions = useMemo(
    () => filterSearchableOptions(orderedOptions, query),
    [orderedOptions, query],
  );
  const selectedOption = orderedOptions.find((option) => option.value === value) || null;
  const isDisabled = disabled || options.length === 0;

  useEffect(() => {
    if (!open) return;
    const selectedIndex = filteredOptions.findIndex((option) => option.value === value && !option.disabled);
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : edgeEnabledOptionIndex(filteredOptions, "first"));
  }, [filteredOptions, open, value]);

  const changeOpen = (nextOpen: boolean) => {
    if (nextOpen && isDisabled) return;
    setOpen(nextOpen);
    if (!nextOpen) setQuery("");
  };

  const selectOption = (option: SearchableSelectOption | undefined) => {
    if (!option || option.disabled) return;
    onValueChange(option.value);
    changeOpen(false);
  };

  const handleListKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => enabledOptionIndex(filteredOptions, current, direction));
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(edgeEnabledOptionIndex(filteredOptions, event.key === "Home" ? "first" : "last"));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      selectOption(filteredOptions[activeIndex]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      changeOpen(false);
      return;
    }
    if (event.key === "Tab") changeOpen(false);
  };

  return (
    <Popover.Root open={open} onOpenChange={changeOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label={ariaLabel}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listboxId : undefined}
          aria-invalid={invalid || undefined}
          disabled={isDisabled}
          className={styles.trigger}
          onKeyDown={(event) => {
            if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
              event.preventDefault();
              changeOpen(true);
            }
          }}
        >
          <span className={styles.selected}>
            {selectedOption?.accent ? <span className={styles.dot} style={{ background: selectedOption.accent }} aria-hidden="true" /> : null}
            <span className={styles.selectedCopy}>
              <span className={selectedOption ? styles.selectedLabel : styles.placeholder}>
                {selectedOption?.label || placeholder}
              </span>
              {selectedOption?.description ? <span className={styles.selectedDescription}>{selectedOption.description}</span> : null}
            </span>
          </span>
          <svg className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`} viewBox="0 0 20 20" aria-hidden="true">
            <path d="m6 8 4 4 4-4" />
          </svg>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className={styles.content}
          align="start"
          sideOffset={6}
          collisionPadding={12}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            window.requestAnimationFrame(() => searchRef.current?.focus());
          }}
        >
          <div className={styles.searchRow}>
            <svg className={styles.searchIcon} viewBox="0 0 20 20" aria-hidden="true">
              <circle cx="8.5" cy="8.5" r="5.5" />
              <path d="m13 13 4 4" />
            </svg>
            <input
              ref={searchRef}
              type="search"
              role="searchbox"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleListKeyDown}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
              aria-controls={listboxId}
              aria-activedescendant={activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
              className={styles.searchInput}
            />
            {query ? (
              <button type="button" className={styles.clear} onClick={() => setQuery("")} aria-label={clearSearchLabel}>×</button>
            ) : null}
          </div>
          <div id={listboxId} role="listbox" aria-label={ariaLabel} className={styles.listbox}>
            {filteredOptions.length === 0 ? <div className={styles.empty}>{emptyText}</div> : null}
            {filteredOptions.map((option, index) => {
              const selected = option.value === value;
              const active = index === activeIndex;
              return (
                <button
                  key={`${option.value}-${index}`}
                  id={`${listboxId}-option-${index}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={option.disabled}
                  data-active={active ? "true" : undefined}
                  className={styles.option}
                  onPointerMove={() => !option.disabled && setActiveIndex(index)}
                  onClick={() => selectOption(option)}
                >
                  {option.accent ? <span className={styles.dot} style={{ background: option.accent }} aria-hidden="true" /> : null}
                  <span className={styles.optionCopy}>
                    <span className={styles.optionLabel}>{option.label}</span>
                    {option.description ? <span className={styles.optionDescription}>{option.description}</span> : null}
                  </span>
                  {selected ? <span className={styles.check} aria-hidden="true">✓</span> : null}
                </button>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
