import { describe, expect, it } from "vitest";

import {
  edgeEnabledOptionIndex,
  enabledOptionIndex,
  filterSearchableOptions,
  sortSearchableOptions,
  type SearchableSelectOption,
} from "@/components/workspace/searchableSelectUtils";

const options: SearchableSelectOption[] = [
  { value: "z", label: "Zulu", description: "trend v2" },
  { value: "a", label: "Alpha", keywords: ["support resistance"] },
  { value: "d", label: "Disabled", disabled: true },
];

describe("searchable select helpers", () => {
  it("sorts labels without mutating the source order", () => {
    expect(sortSearchableOptions(options).map((option) => option.value)).toEqual(["a", "d", "z"]);
    expect(options.map((option) => option.value)).toEqual(["z", "a", "d"]);
  });

  it("matches labels, descriptions, and keywords case-insensitively", () => {
    expect(filterSearchableOptions(options, "TREND").map((option) => option.value)).toEqual(["z"]);
    expect(filterSearchableOptions(options, "resistance").map((option) => option.value)).toEqual(["a"]);
  });

  it("skips disabled options during keyboard navigation", () => {
    expect(enabledOptionIndex(options, 1, 1)).toBe(0);
    expect(enabledOptionIndex(options, 0, -1)).toBe(1);
    expect(edgeEnabledOptionIndex(options, "first")).toBe(0);
    expect(edgeEnabledOptionIndex(options, "last")).toBe(1);
  });
});
