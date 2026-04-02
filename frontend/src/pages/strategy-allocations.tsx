import { useEffect } from "react";
import { useRouter } from "next/router";

export default function StrategyAllocationsRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/paper-trading#allocations");
  }, [router]);

  return <p style={{ padding: 24 }}>正在跳转到 Paper Trading 工作台...</p>;
}
