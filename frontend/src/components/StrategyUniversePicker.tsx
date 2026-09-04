import { useState } from "react";
import { StockBasketSelector } from "@/components/StockBasketSelector";
import { WorkspaceDialog } from "@/components/workspace/WorkspaceDialog";
import { useI18n } from "@/i18n/provider";

export function StrategyUniversePicker({ symbols, onChange }: {
  symbols: string[];
  onChange: (symbols: string[]) => void;
}) {
  const { messages } = useI18n();
  const copy = messages.strategyCreate.basics;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const preview = symbols.slice(0, 8).join(", ");
  return <div style={{ display: "grid", gap: 10 }}>
    <div style={{ padding: 12, border: "1px solid rgba(71,85,105,.4)", borderRadius: 12, background: "rgba(15,23,42,.5)", color: "#a5f3fc", fontSize: 13, lineHeight: 1.6, overflowWrap: "anywhere" }}>
      {symbols.length ? copy.universeCount.replace("{count}", String(symbols.length)).replace("{symbols}", preview) : copy.universeEmpty}
      {symbols.length > 8 ? <span> · {copy.universeMore.replace("{count}", String(symbols.length - 8))}</span> : null}
    </div>
    <WorkspaceDialog title={copy.universeAdjust} triggerLabel={copy.universeAdjust} description={copy.universeEmpty}
      open={open} onOpenChange={(next) => { if (next) setDraft(symbols.slice()); setOpen(next); }} size="form"
      footer={<button type="button" onClick={() => { onChange(draft); setOpen(false); }}
        style={{ padding: "9px 14px", borderRadius: 10, border: "1px solid #0891b2", background: "#078cad", color: "#fff", cursor: "pointer" }}>{copy.universeApply}</button>}>
      {open ? <StockBasketSelector symbols={draft} onChange={setDraft} /> : null}
    </WorkspaceDialog>
  </div>;
}
