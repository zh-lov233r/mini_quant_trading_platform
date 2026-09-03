import { useCallback, useEffect, useRef, useState } from "react";
import { getDashboardOverview } from "@/api/dashboard";
import type { DashboardOverview } from "@/types/dashboard";

export function useDashboardOverview() {
  const [data, setData] = useState<DashboardOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [refreshing, setRefreshing] = useState(true);
  const mounted = useRef(false);
  const busy = useRef(false);
  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setRefreshing(true);
    try {
      const next = await getDashboardOverview();
      if (mounted.current) { setData(next); setFailed(false); }
    } catch {
      if (mounted.current) setFailed(true);
    } finally {
      busy.current = false;
      if (mounted.current) setRefreshing(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const poll = () => { if (document.visibilityState === "visible") void refresh(); };
    const timer = window.setInterval(poll, 30_000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [refresh]);
  return { data, failed, refreshing, refresh };
}
