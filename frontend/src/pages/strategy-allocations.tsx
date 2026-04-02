import { useEffect } from "react";
import { useRouter } from "next/router";

import { useI18n } from "@/i18n/provider";

export default function StrategyAllocationsRedirectPage() {
  const router = useRouter();
  const { locale } = useI18n();

  useEffect(() => {
    router.replace("/paper-trading#allocations");
  }, [router]);

  return (
    <p style={{ padding: 24 }}>
      {locale === "zh-CN"
        ? "正在跳转到 Paper Trading 工作台..."
        : "Redirecting to the Paper Trading workspace..."}
    </p>
  );
}
