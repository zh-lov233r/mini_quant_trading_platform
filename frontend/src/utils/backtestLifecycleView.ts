export type LifecycleChartDisplayState = "loading" | "error" | "empty" | "chart";

export function lifecycleChartDisplayState({
  loading,
  error,
  barCount,
}: {
  loading: boolean;
  error: string | null;
  barCount: number;
}): LifecycleChartDisplayState {
  if (error) return "error";
  // Keep the existing chart mounted during a range reload. Replacing it with
  // a short loading box would shrink the scroll container and move the user.
  if (barCount > 0) return "chart";
  if (loading) return "loading";
  return "empty";
}
