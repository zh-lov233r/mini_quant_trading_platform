import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect,useState } from "react";
import AppShell from "@/components/AppShell";
import { SignalStatusBadge } from "@/components/signals/SignalStatusBadge";
import { StrategyInstanceLabel } from "@/components/signals/StrategyInstanceLabel";
import { useI18n } from "@/i18n/provider";
import { signalMessages,signalStatus } from "@/i18n/messages/signals";
import { SelectControl } from "@/components/workspace/SelectControl";
import { SignalChartWorkspace } from "@/components/signals/SignalChartWorkspace";
import * as api from "@/api/signals";
import { ApiError } from "@/api/client";
import type { Page,Observation,Report } from "@/types/signals";
import styles from "@/components/signals/Signals.module.css";
export default function SignalReportPage(){
  const router=useRouter();const id=String(router.query.reportId||"");const {locale}=useI18n();const t=signalMessages[locale];
  const [report,setReport]=useState<Report|null>(null);const [rows,setRows]=useState<Page<Observation>>({items:[],total:0});const [error,setError]=useState("");const [loading,setLoading]=useState(true);const [recipients,setRecipients]=useState("");
  const offset=Number(router.query.offset||0);const q=String(router.query.q||"");const filter=String(router.query.strategy||"");const pass=String(router.query.passes??"true");const grouped=router.query.group==="instrument";
  const selected=String(router.query.signal_id||"");const instrument=Number(router.query.instrument_id||0);const strategy=String(router.query.strategy_run_id||"");
  useEffect(()=>{if(!id)return;let active=true;const load=()=>api.getSignalReport(id).then(r=>{if(active)setReport(r);}).catch(e=>{if(active){setError(e instanceof ApiError&&e.status===410?t.expired:String(e));if(e instanceof ApiError&&e.status===410)setReport(null);}});void load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer);};},[id,t.expired]);
  useEffect(()=>{if(!id)return;let active=true;setLoading(true);const params=new URLSearchParams({offset:String(offset),q, ...(filter?{strategy_run_id:filter}:{}), passes:pass,group:grouped?"instrument":"strategy"});
    api.getSignalObservations(id,params.toString()).then(r=>{if(active)setRows(r);}).catch(e=>{if(active)setError(e instanceof ApiError&&e.status===410?t.expired:String(e));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[id,offset,q,filter,pass,grouped,t.expired]);
  const ordered=rows.items;
  const index=ordered.findIndex(o=>o.id===selected);
  function query(values:Record<string,string>){void router.push({pathname:router.pathname,query:{...router.query,...values}},undefined,{shallow:true,scroll:false});}
  function choose(o:Observation){query(selected===o.id?{signal_id:"",instrument_id:"",strategy_run_id:""}:{signal_id:o.id,instrument_id:String(o.instrument_id),strategy_run_id:o.strategy_run_id});}
  async function adjacent(direction:number){
    const next=index+direction;
    if(next>=0&&next<ordered.length){choose(ordered[next]);return;}
    const pageOffset=offset+direction*50;
    if(pageOffset<0||pageOffset>=rows.total)return;
    const params=new URLSearchParams({offset:String(pageOffset),q,passes:pass,group:grouped?"instrument":"strategy",...(filter?{strategy_run_id:filter}:{})});
    try { const page=await api.getSignalObservations(id,params.toString());const o=direction>0?page.items[0]:page.items.at(-1);
      if(o)query({offset:String(pageOffset),signal_id:o.id,instrument_id:String(o.instrument_id),strategy_run_id:o.strategy_run_id});
    }catch(e){setError(e instanceof ApiError&&e.status===410?t.expired:String(e));}
  }
  async function act(fn:()=>Promise<unknown>){try{setError("");await fn();setReport(await api.getSignalReport(id));}catch(e){setError(e instanceof ApiError&&e.status===410?t.expired:String(e));}}
  if(error===t.expired)return <AppShell title={t.expired} actions={<Link className={styles.button} href="/signals">{t.back}</Link>}><p role="status">{t.expired}</p></AppShell>;
  return <AppShell title={report?.summary.name||t.report} actions={<Link className={styles.button} href="/signals">{t.back}</Link>}>
    {error&&<p className={styles.error} role="alert">{error}</p>}
    {report&&<section className={styles.section}><div className={styles.toolbar}><span>{report.summary.market} · {report.summary.session_date}</span><SignalStatusBadge status={report.status}/><span>{t.coverage}: {signalStatus(report.summary.status,locale)}</span>{report.formats?.map(fmt=><a className={styles.button} key={fmt} href={api.signalExportUrl(id,fmt)}>{t.export} {fmt.toUpperCase()}</a>)}</div>
      <div className={styles.metrics}><div className={styles.metric}><small>{t.signals}</small><strong>{report.summary.observation_count??"—"}</strong><span>{t.passed}</span></div><div className={styles.metric}><small>{t.stocks}</small><strong>{report.summary.instrument_count??"—"}</strong><span>{t.coverage}</span></div><div className={styles.metric}><small>{t.strategies}</small><strong>{report.summary.completed_strategies??0}/{report.summary.strategy_count??0}</strong><span>{t.coverage}</span></div></div>
      <p className={styles.hint}>{t.expires}: {report.expires_at?new Date(report.expires_at).toLocaleString(locale):report.retention_reason?t.calendar:"—"}</p>
      {report.document?.strategy_sections?.map(s=><p key={s.id} className={styles.hint}><StrategyInstanceLabel name={s.name} version={s.version} type={s.strategy_type}/> · {signalStatus(s.status,locale)} · {t.coverage} {s.coverage.evaluated}/{s.coverage.expected} · {Object.keys(s.coverage.reasons).length>0&&JSON.stringify(s.coverage.reasons)} {s.error}</p>)}
      {report.render_status&&<div className={styles.toolbar} role="status"><span className={styles.hint}>{t.rendering}</span><SignalStatusBadge status={report.render_status}/></div>}
      {(Object.keys(report.render_errors).length>0||report.render_status==="failed"||report.status==="failed")&&<div className={styles.error}>{report.error} {report.render_error} {JSON.stringify(report.render_errors)} <button className={styles.button} disabled={report.render_status==="running"||report.render_status==="queued"} onClick={()=>void act(()=>api.retrySignalRendering(id))}>{t.retry}</button></div>}
      <div className={styles.toolbar}><input aria-label={t.recipients} placeholder={t.recipients} value={recipients} onChange={e=>setRecipients(e.target.value)}/><button className={`${styles.button} ${styles.primary}`} disabled={!recipients||report.status!=="completed"} onClick={()=>void act(()=>api.deliverSignalReport(id,recipients.split(",").map(s=>s.trim()).filter(Boolean)))}>{t.send}</button></div>
      {report.deliveries?.map(d=><p key={d.id} className={styles.hint}>{d.recipient} · <SignalStatusBadge status={d.status}/> {d.error} {d.status==="failed"&&<button className={styles.button} onClick={()=>void act(()=>api.retrySignalDelivery(id,d.id))}>{t.retry}</button>}</p>)}
    </section>}
    <section className={styles.section}><div className={styles.toolbar}>
      <input aria-label={t.search} placeholder={t.search} value={q} onChange={e=>query({q:e.target.value,offset:"0"})}/>
      <SelectControl aria-label={t.strategies} value={filter||"all"} onValueChange={v=>query({strategy:v==="all"?"":v,offset:"0"})} options={[{value:"all",label:t.all},...(report?.document?.strategy_sections||[]).map(s=>({value:s.id,label:s.name}))]}/>
      <SelectControl aria-label={t.status} value={pass} onValueChange={v=>query({passes:v,offset:"0"})} options={[{value:"true",label:t.passed},{value:"false",label:t.filtered},{value:"all",label:t.audit}]}/>
      <button className={styles.button} onClick={()=>query({group:grouped?"strategy":"instrument",offset:"0"})}>{grouped?t.byStrategy:t.grouped}</button>
    </div>{loading&&<p role="status">{t.loading}</p>}<div className={styles.scroll}><table className={styles.table}><thead><tr><th>{t.stocks}</th><th>{t.strategies}</th><th>{t.signals}</th><th>{t.evidence}</th></tr></thead><tbody>{ordered.map(o=><tr key={o.id} className={`${styles.interactiveRow} ${selected===o.id?styles.selected:""}`} onClick={()=>choose(o)}><td className={styles.stockCell}><button className={styles.stockToggle} aria-expanded={selected===o.id} aria-controls="signal-chart" onClick={event=>{event.stopPropagation();choose(o);}}><span>{o.symbol_as_of}</span><span aria-hidden="true">{selected===o.id?"−":"+"}</span></button></td><td><StrategyInstanceLabel name={o.strategy_name} version={o.strategy_version} type={o.strategy_type}/></td><td>{o.event_type}<br/>{o.strength?.score.toFixed(1)??"—"}</td><td>{o.reason}</td></tr>)}</tbody></table>{!loading&&!rows.total&&<p className={styles.empty}>{t.empty}</p>}</div>
      <div className={styles.pagination}><button className={styles.button} disabled={!offset} onClick={()=>query({offset:String(Math.max(0,offset-50))})}>{t.previous}</button><span>{rows.total}</span><button className={styles.button} disabled={offset+50>=rows.total} onClick={()=>query({offset:String(offset+50)})}>{t.next}</button></div>
    </section>
    {report&&selected&&instrument>0&&strategy&&<section className={styles.section}><div className={styles.toolbar}><button className={styles.button} disabled={index<0||(index===0&&offset===0)} onClick={()=>void adjacent(-1)}>{t.previousSignal}</button><button className={styles.button} disabled={index<0||offset+index+1>=rows.total} onClick={()=>void adjacent(1)}>{t.nextSignal}</button></div><SignalChartWorkspace sourceId={id} instrument={instrument} strategy={strategy} signal={selected}/></section>}
  </AppShell>;
}
