export interface SearchableSelectOption {
  value: string;
  label: string;
  description?: string;
  keywords?: string[];
  accent?: string;
  disabled?: boolean;
}

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase();
}

export function sortSearchableOptions(options: readonly SearchableSelectOption[]): SearchableSelectOption[] {
  return options
    .map((option, index) => ({ option, index }))
    .sort((left, right) => left.option.label.localeCompare(right.option.label) || left.index - right.index)
    .map(({ option }) => option);
}

export function filterSearchableOptions(
  options: readonly SearchableSelectOption[],
  query: string,
): SearchableSelectOption[] {
  const needle = normalized(query);
  if (!needle) return [...options];
  return options.filter((option) => (
    [option.label, option.description || "", ...(option.keywords || [])]
      .some((value) => normalized(value).includes(needle))
  ));
}

export function enabledOptionIndex(
  options: readonly SearchableSelectOption[],
  currentIndex: number,
  direction: 1 | -1,
): number {
  if (!options.length) return -1;
  for (let step = 1; step <= options.length; step += 1) {
    const index = (currentIndex + direction * step + options.length) % options.length;
    if (!options[index]?.disabled) return index;
  }
  return -1;
}

export function edgeEnabledOptionIndex(
  options: readonly SearchableSelectOption[],
  edge: "first" | "last",
): number {
  const indexes = edge === "first"
    ? options.map((_, index) => index)
    : options.map((_, index) => options.length - index - 1);
  return indexes.find((index) => !options[index]?.disabled) ?? -1;
}
