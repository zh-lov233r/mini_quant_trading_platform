// 简单首页：给到“新建趋势策略”的入口
import Link from "next/link";

export default function Home() {
  return (
    <main style={{ maxWidth: 680, margin: "48px auto", fontFamily: "system-ui" }}>
      <h1 style={{ marginBottom: 12 }}>Quant Frontend</h1>
      <p style={{ marginBottom: 16 }}>This is Main Page, Create a New Strategy!</p>
      <Link href="/strategies/new" style={{ color: "#2563eb" }}>
        /strategies/new
      </Link>
    </main>
  );
}
