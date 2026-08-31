export type SelectControlValue = string | number;

export function normalizeSelectControlValue(value: SelectControlValue): string {
  return String(value);
}
