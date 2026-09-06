import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { getSignalChart } from "@/api/signals";
import { useI18n } from "@/i18n/provider";
import { signalMessages } from "@/i18n/messages/signals";
import type { ChartSnapshot } from "@/types/signals";
import { object,signalLayers } from "./signalPresentation";
import { SelectControl } from "@/components/workspace/SelectControl";
import styles from "./Signals.module.css";
const Chart=dynamic(()=>import("@/components/charts/CandlestickLightweightChart"),{ssr:false});
export function SignalChartWorkspace({sourceId,instrument,strategy,signal}:{sourceId:string;instrument:number;strategy:string;signal:string}) {
  const {locale}=useI18n();const t=signalMessages[locale];const [snapshot,setSnapshot]=useState<ChartSnapshot|null>(null);
  const [range,setRange]=useState(120);const [before,setBefore]=useState<string>();const [error,setError]=useState("");const [loading,setLoading]=useState(false);
  const [layers,setLayers]=useState(true);const [expanded,setExpanded]=useState(false);const [reset,setReset]=useState(0);
  useEffect(()=>{setBefore(undefined);},[signal,instrument]);
  useEffect(()=>{let active=true;setLoading(true);setError("");
    getSignalChart(sourceId,instrument,strategy,signal,range,before).then(next=>{if(!active)return;setSnapshot(old=>{
      if(before&&old&&old.snapshot_id===next.snapshot_id&&old.observation.id===signal){const bars=[...next.bars,...old.bars];return {...next,bars:Array.from(new Map(bars.map(b=>[b.trade_date,b])).values()).sort((a,b)=>a.trade_date.localeCompare(b.trade_date)),indicator_series:next.indicator_series.map(i=>({...i,values:[...i.values,...(old.indicator_series.find(j=>j.key===i.key)?.values||[])]}))};}return next;
    });}).catch(e=>{if(active)setError(String(e));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};
  },[sourceId,instrument,strategy,signal,range,before]);
  const shown=snapshot?.instrument_id===instrument&&snapshot.report_id===sourceId?snapshot:null;
  const overlays=useMemo(()=>shown?.observation.id===signal?signalLayers(shown):{markers:[],zones:[]},[shown,signal]);
  return <div className={styles.chart} id="signal-chart"><div className={styles.toolbar}>
    <SelectControl aria-label={t.locate} value={range} onValueChange={v=>{setRange(Number(v));setBefore(undefined);setReset(r=>r+1);}} options={[60,120,250].map(v=>({value:v,label:String(v)}))}/>
    <button className={styles.button} onClick={()=>{setBefore(undefined);setReset(r=>r+1);}}>{t.locate}</button>
    <button className={styles.button} disabled={loading||!shown?.next_cursor} onClick={()=>setBefore(shown?.next_cursor||undefined)}>{t.older}</button>
    <label><input type="checkbox" checked={layers} onChange={e=>setLayers(e.target.checked)}/>{t.layers}</label>
    <button className={styles.button} onClick={()=>setExpanded(!expanded)} aria-pressed={expanded}>{t.enlarge}</button>{loading&&<span role="status">{t.loading}</span>}
  </div>{error&&<p role="alert" className={styles.error}>{error}</p>}
  {shown&&<><h2>{shown.symbol_as_of} · {shown.observation.strategy_name}</h2><p className={styles.hint}>{shown.market} · {shown.as_of_session} · {shown.session_timezone} · {shown.price_semantics}<br/>{t.snapshot}: {shown.snapshot_id.slice(0,16)} · {shown.available_from} — {shown.available_to}</p>
    <Chart bars={shown.bars} locale={locale} height={expanded?640:420} markers={layers?overlays.markers:[]} zones={layers?overlays.zones:[]} indicators={layers?shown.indicator_series:[]} viewKey={`report:${sourceId}:${instrument}`} resetKey={`${reset}`} ariaLabel={`${shown.symbol_as_of} ${t.report}`}/>
    {!shown.next_cursor&&<p className={styles.hint}>{t.remaining}</p>}
    {shown.observation.id===signal&&<><h3>{t.evidence} · {shown.observation.event_type}</h3><p>{String(shown.observation.evidence.reason||"")}</p>
    <div className={styles.evidence}>{[[t.dimensions,object(shown.observation.evidence.observations)],[t.thresholds,object(shown.observation.evidence.parameters)]].map(([title,fields])=><div key={String(title)}><h4>{String(title)}</h4><table className={styles.table}><tbody>{Object.entries(fields).map(([key,value])=><tr key={key}><th>{key}</th><td>{value===null?t.missing:typeof value==="object"?JSON.stringify(value):String(value)}</td></tr>)}</tbody></table></div>)}</div>
    <details><summary>{t.raw}</summary><div className={styles.evidence}><pre>{JSON.stringify(shown.observation.evidence,null,2)}</pre></div></details></>}
  </>}
  </div>;
}
