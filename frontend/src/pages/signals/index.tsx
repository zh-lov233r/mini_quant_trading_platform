import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AppShell from "@/components/AppShell";
import { SignalStatusBadge } from "@/components/signals/SignalStatusBadge";
import { SignalStrategyChoiceCard } from "@/components/signals/SignalStrategyChoiceCard";
import { StrategyInstanceLabel } from "@/components/signals/StrategyInstanceLabel";
import { SearchableSelect } from "@/components/workspace/SearchableSelect";
import { SelectControl } from "@/components/workspace/SelectControl";
import { useI18n } from "@/i18n/provider";
import { signalMessages } from "@/i18n/messages/signals";
import { listStrategies, getStrategyCatalog } from "@/api/strategies";
import { listStockBaskets } from "@/api/stock-baskets";
import * as api from "@/api/signals";
import type { Plan, Report, Page } from "@/types/signals";
import type { StrategyOut } from "@/types/strategy";
import styles from "@/components/signals/Signals.module.css";

export default function SignalsPage() {
  const { locale } = useI18n(); const t = signalMessages[locale];
  const [strategies,setStrategies]=useState<StrategyOut[]>([]); const [supported,setSupported]=useState<string[]>([]);
  const [baskets,setBaskets]=useState<{id:string;name:string}[]>([]); const [basket,setBasket]=useState("");
  const [selected,setSelected]=useState<string[]>([]); const [name,setName]=useState(""); const [market,setMarket]=useState<"US"|"CN">("US");
  const [session,setSession]=useState(""); const [scheduled,setScheduled]=useState(false); const [editing,setEditing]=useState<string>();
  const [reportLanguage,setReportLanguage]=useState<"zh-CN"|"en-US">(locale); const [recipients,setRecipients]=useState(""); const [retention,setRetention]=useState(3); const [enabled,setEnabled]=useState(false);
  const [plans,setPlans]=useState<Plan[]>([]); const [reports,setReports]=useState<Page<Report>>({items:[],total:0});
  const [reportOffset,setReportOffset]=useState(0); const [error,setError]=useState(""); const [busy,setBusy]=useState(false);const [notice,setNotice]=useState("");
  const refresh=useCallback(async()=>{const [p,r]=await Promise.all([api.listSignalPlans(),api.listSignalReports(reportOffset)]);setPlans(p.items);setReports(r);},[reportOffset]);
  useEffect(()=>{Promise.all([listStrategies(),listStockBaskets(),getStrategyCatalog()]).then(([s,b,c])=>{setStrategies(s);setBaskets(b);setSupported(c.map(x=>x.strategy_type));}).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{let active=true;const load=()=>refresh().catch(e=>{if(active)setError(String(e));});void load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer);};},[refresh]);
  async function action(fn:()=>Promise<unknown>){setBusy(true);setError("");setNotice("");try{await fn();await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  function edit(plan:Plan){setEditing(plan.id);setScheduled(true);setName(plan.name);setMarket(plan.market);setBasket(plan.basket_id);setSelected(plan.strategies.map(s=>s.strategy_id));setRecipients(plan.recipients.join(", "));setRetention(plan.retention_sessions);setReportLanguage(plan.language);setEnabled(plan.enabled);window.scrollTo({top:0,behavior:"auto"});}
  return <AppShell title={t.title} actions={<button className={styles.button} disabled={busy} onClick={()=>void refresh().catch(e=>setError(String(e)))}>{t.refresh}</button>}><p className={styles.intro}>{t.overview}</p><section className={styles.section}>
    <div className={styles.modeSwitch}><button className={`${styles.button} ${styles.mode}`} aria-pressed={!scheduled} onClick={()=>{setScheduled(false);setEditing(undefined);}}>{t.manual}</button><button className={`${styles.button} ${styles.mode}`} aria-pressed={scheduled} onClick={()=>{setScheduled(true);setEditing(undefined);setEnabled(false);}}>{t.create}</button></div>
    <h2>{scheduled?t.scheduled:t.manual}</h2><p className={styles.hint}>{scheduled?t.scheduledHint:t.manualHint}</p>
    <form onSubmit={e=>{e.preventDefault();void action(async()=>{const body={name:name||t.title,market,basket_id:basket,strategy_ids:selected,session_date:session||null};if(scheduled)await api.saveSignalPlan({...body,language:reportLanguage,recipients:recipients.split(",").map(x=>x.trim()).filter(Boolean),retention_sessions:retention,enabled},editing);else{await api.startSignalScan(body);setNotice(t.scanQueued);}});}}>
      <div className={styles.form}>
        <label>{t.name}<input value={name} onChange={e=>setName(e.target.value)} required maxLength={128}/></label>
        <label>{t.market}<SelectControl value={market} onValueChange={v=>setMarket(v==="CN"?"CN":"US")} options={[{value:"US",label:"US"},{value:"CN",label:"A 股 / China"}]}/></label>
        <label>{t.basket}<SearchableSelect value={basket} onValueChange={setBasket} ariaLabel={t.basket} options={baskets.map(b=>({value:b.id,label:b.name}))} placeholder={t.basket} searchPlaceholder={t.searchBasket} emptyText={t.empty}/></label>
        {!scheduled&&<label>{t.session}<input type="date" value={session} onChange={e=>setSession(e.target.value)}/></label>}
        <fieldset className={styles.strategies}><legend>{t.strategies} · {t.selected} {selected.length}</legend><div className={styles.strategyOptions}>{strategies.map(s=>{
          const isSupported=supported.includes(s.strategy_type);
          return <SignalStrategyChoiceCard key={s.id} strategy={s} locale={locale} selected={selected.includes(s.id)} supported={isSupported} supportedLabel={t.scannable} unsupportedLabel={t.unsupported} onChange={checked=>setSelected(checked?[...selected,s.id]:selected.filter(id=>id!==s.id))}/>;
        })}</div></fieldset>
        {scheduled&&<><label>{t.language}<SelectControl value={reportLanguage} onValueChange={v=>setReportLanguage(v==="zh-CN"?"zh-CN":"en-US")} options={[{value:"zh-CN",label:"中文"},{value:"en-US",label:"English"}]}/></label><label>{t.recipients}<input value={recipients} onChange={e=>setRecipients(e.target.value)}/></label><label>{t.retention}<input type="number" min={1} max={365} value={retention} onChange={e=>setRetention(Number(e.target.value))}/></label></>}
      </div><div className={`${styles.toolbar} ${styles.submitBar}`}><button className={`${styles.button} ${styles.primary}`} disabled={busy||!basket||!selected.length}>{busy?t.loading:scheduled?t.save:t.scan}</button></div>
    </form>{error&&<p role="alert" className={styles.error}>{error}</p>}{notice&&<p className={styles.notice} role="status">{notice}</p>}
  </section>
  <section className={styles.section}><div className={styles.sectionHeading}><h2>{t.scheduled}</h2><span className={styles.count}>{plans.length}</span></div><div className={styles.scroll}><table className={styles.table}><thead><tr><th>{t.name}</th><th>{t.strategies}</th><th>{t.retention}</th><th>{t.status}</th><th/></tr></thead><tbody>{!plans.length&&<tr><td colSpan={5}><p className={styles.empty}>{t.emptyPlans}</p></td></tr>}{plans.map(p=><tr key={p.id}><td><div className={styles.identity}><strong>{p.name}</strong><small>{p.market}</small></div></td><td><div className={styles.instanceList}>{p.strategies.map(s=><StrategyInstanceLabel key={s.strategy_id} name={s.name} version={s.version} type={s.strategy_type}/>)}</div></td><td>{p.retention_sessions}</td><td><SignalStatusBadge status={p.enabled?"enabled":"disabled"}/></td><td><div className={styles.actions}><button className={styles.button} onClick={()=>edit(p)}>{t.edit}</button> <button className={styles.button} disabled={busy} onClick={()=>void action(()=>api.toggleSignalPlan(p.id,!p.enabled))}>{p.enabled?t.disable:t.enable}</button></div></td></tr>)}</tbody></table></div></section>
  <section className={styles.section}><div className={styles.sectionHeading}><h2>{t.reports}</h2><span className={styles.count}>{reports.total}</span></div><div className={styles.scroll}><table className={styles.table}><thead><tr><th>{t.name}</th><th>{t.date}</th><th>{t.status}</th><th>{t.signals}</th><th>{t.expires}</th></tr></thead><tbody>{!reports.items.length&&<tr><td colSpan={5}><p className={styles.empty}>{t.emptyReports}</p></td></tr>}{reports.items.map(r=><tr key={r.id}><td>{r.expired_at?r.summary.name:<Link href={`/signals/reports/${r.id}`}>{r.summary.name||r.id.slice(0,8)}</Link>}</td><td>{r.summary.session_date}</td><td><SignalStatusBadge status={r.status}/></td><td>{r.summary.observation_count??"—"}</td><td>{r.expired_at?t.expired:r.expires_at?new Date(r.expires_at).toLocaleString(locale):r.retention_reason?t.calendar:"—"}</td></tr>)}</tbody></table></div><div className={styles.pagination}><span>{reports.total?reportOffset+1:0}–{Math.min(reportOffset+20,reports.total)} / {reports.total}</span><button className={styles.button} disabled={!reportOffset} onClick={()=>setReportOffset(Math.max(0,reportOffset-20))}>{t.previous}</button><button className={styles.button} disabled={reportOffset+20>=reports.total} onClick={()=>setReportOffset(reportOffset+20)}>{t.next}</button></div></section>
  </AppShell>;
}
