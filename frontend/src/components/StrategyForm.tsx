import React, { useState } from "react";
import { createStrategy } from "@/api/strategies";

export default function StrategyForm() {
  const [name, setName] = useState("Trend_EMA15_SMA200");
  const [status, setStatus] = useState<"draft" | "active">("draft");
  const [emaShort, setEmaShort] = useState("EMA15");
  const [smaLong, setSmaLong] = useState("SMA200");
  const [volMul, setVolMul] = useState(1.5);
  const [atrMul, setAtrMul] = useState(2.0);
  const [resp, setResp] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null); setResp(null); setLoading(true);
    try {
      // 前端轻校验：形如 EMA15 / SMA200、倍数 > 0
      if (!/^EMA\d+$/.test(emaShort)) throw new Error("短期EMA格式应为 EMA+数字，例如 EMA15");
      if (!/^SMA\d+$/.test(smaLong))  throw new Error("长期SMA格式应为 SMA+数字，例如 SMA200");
      if (!(Number(volMul) > 0)) throw new Error("成交量过滤倍数必须 > 0");
      if (!(Number(atrMul) > 0)) throw new Error("ATR乘数必须 > 0");

      const payload = {
        name,
        strategy_type: "trend" as const,
        status,
        params: {
          ema_short: emaShort,
          sma_long: smaLong,
          volume_multiplier: Number(volMul),
          atr_multiplier: Number(atrMul),
        },
      };
      const idem = (crypto as any)?.randomUUID?.() || String(Date.now());
      const data = await createStrategy(payload, idem);
      setResp(data);
    } catch (e: any) {
      setErr(e?.message || "提交失败");
    } finally {
      setLoading(false);
    }
  };

  const Box: React.CSSProperties = { display:"flex", flexDirection:"column", gap:8, margin:"0 0 12px" };
  const Input: React.CSSProperties = { padding:8, border:"1px solid #ddd", borderRadius:6 };

  return (
    <form onSubmit={submit} style={{ maxWidth: 680, margin: "32px auto", fontFamily:"system-ui, -apple-system" }}>
      <h2 style={{ marginBottom: 16 }}>新建趋势策略</h2>

      <div style={Box}>
        <label>策略名</label>
        <input style={Input} value={name} onChange={(e)=>setName(e.target.value)} required />
      </div>

      <div style={Box}>
        <label>状态</label>
        <select style={Input} value={status} onChange={(e)=>setStatus(e.target.value as any)}>
          <option value="draft">draft</option>
          <option value="active">active</option>
        </select>
      </div>

      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:12 }}>
        <div style={Box}>
          <label>短期 EMA (如 EMA15)</label>
          <input style={Input} value={emaShort} onChange={(e)=>setEmaShort(e.target.value)} required />
        </div>
        <div style={Box}>
          <label>长期 SMA (如 SMA200)</label>
          <input style={Input} value={smaLong} onChange={(e)=>setSmaLong(e.target.value)} required />
        </div>
        <div style={Box}>
          <label>成交量过滤倍数 (大于20日均量)</label>
          <input type="number" step="0.1" style={Input}
                 value={volMul} onChange={(e)=>setVolMul(parseFloat(e.target.value))} required />
        </div>
        <div style={Box}>
          <label>ATR 波动率乘数</label>
          <input type="number" step="0.1" style={Input}
                 value={atrMul} onChange={(e)=>setAtrMul(parseFloat(e.target.value))} required />
        </div>
      </div>

      <button type="submit" disabled={loading}
              style={{ marginTop:16, padding:"10px 16px", borderRadius:8, border:0, background:"#111827", color:"#fff" }}>
        {loading ? "提交中…" : "提交"}
      </button>

      {err && <div style={{ color:"crimson", marginTop:12 }}>{err}</div>}
      {resp && <pre style={{ background:"#f6f8fa", padding:12, marginTop:12, borderRadius:8 }}>
        {JSON.stringify(resp, null, 2)}
      </pre>}
    </form>
  );
}
