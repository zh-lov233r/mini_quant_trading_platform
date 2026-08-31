import { useEffect, useState } from "react";

interface ProbeSample {
  chartReadyMs: number;
  fps: number;
  longTaskCount: number;
  maxLongTaskMs: number;
}

export default function BacktestPerformanceProbe() {
  const [sample, setSample] = useState<ProbeSample | null>(null);

  useEffect(() => {
    const startedAt = performance.now();
    let frameId = 0;
    let timeoutId = 0;
    let completed = false;
    const longTasks: number[] = [];
    const performanceObserver = typeof PerformanceObserver === "undefined" ? null : new PerformanceObserver((list) => {
      list.getEntries().forEach((entry) => longTasks.push(entry.duration));
    });

    try {
      performanceObserver?.observe({ entryTypes: ["longtask"] });
    } catch {
      performanceObserver?.disconnect();
    }

    const measureFrames = (chartReadyMs: number) => {
      let frames = 0;
      const frameStartedAt = performance.now();
      const tick = (now: number) => {
        frames += 1;
        const elapsed = now - frameStartedAt;
        if (elapsed < 1000) {
          frameId = requestAnimationFrame(tick);
          return;
        }
        completed = true;
        performanceObserver?.disconnect();
        setSample({
          chartReadyMs: Math.round(chartReadyMs * 10) / 10,
          fps: Math.round((frames * 1000 / elapsed) * 10) / 10,
          longTaskCount: longTasks.length,
          maxLongTaskMs: longTasks.length ? Math.round(Math.max(...longTasks) * 10) / 10 : 0,
        });
      };
      frameId = requestAnimationFrame(tick);
    };

    const checkChart = () => {
      if (document.querySelector("canvas")) {
        observer.disconnect();
        measureFrames(performance.now() - startedAt);
      }
    };
    const observer = new MutationObserver(checkChart);
    observer.observe(document.body, { childList: true, subtree: true });
    checkChart();
    timeoutId = window.setTimeout(() => {
      if (!completed) {
        observer.disconnect();
        performanceObserver?.disconnect();
      }
    }, 15000);

    return () => {
      observer.disconnect();
      performanceObserver?.disconnect();
      cancelAnimationFrame(frameId);
      window.clearTimeout(timeoutId);
    };
  }, []);

  return (
    <output hidden data-performance-complete={sample ? "true" : "false"} data-testid="backtest-performance-probe">
      {sample ? JSON.stringify(sample) : "pending"}
    </output>
  );
}
