export const LIFECYCLE_INTERACTIVE_TARGET_SELECTOR =
  "button, input, label, select, textarea, a, [role='button'], [role='img']";

export function isLifecycleInteractiveTarget(
  target: Pick<Element, "closest"> | null,
): boolean {
  return Boolean(target?.closest(LIFECYCLE_INTERACTIVE_TARGET_SELECTOR));
}
