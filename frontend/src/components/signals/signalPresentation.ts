import type { ChartSnapshot, Json } from "@/types/signals";
import type { ChartOverlayMarker, ChartZoneOverlay } from "@/components/charts/chartModels";
export function object(value: Json | undefined): Record<string,Json> { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }
function numeric(v:Json|undefined):number|null { return typeof v === "number" && Number.isFinite(v) ? v : null; }
export function signalLayers(snapshot:ChartSnapshot): {markers:ChartOverlayMarker[];zones:ChartZoneOverlay[]} {
  const o=snapshot.observation;const current=snapshot.bars.find(b=>b.trade_date===o.session_date);
  const markers:ChartOverlayMarker[]=[{key:o.id,label:o.event_type,date:o.session_date,price:current?.close??null,tone:"#5eead4",description:String(o.evidence.reason||o.event_type)}];
  const setup=object(o.evidence.setup);const anchors=object(setup.anchors);
  for(const [name,date] of Object.entries(anchors)) {
    if(typeof date!=="string")continue;
    const bar=snapshot.bars.find(b=>b.trade_date===date);
    const explicit=numeric(anchors[name.replace(/_trade_date$/, "_price")]);
    const lowAnchor=["head","left_shoulder","right_shoulder","bottom","left_bottom_trade_date","right_bottom_trade_date"].includes(name);
    const price=explicit??(lowAnchor?bar?.low:null);
    if(bar&&price!==null&&price!==undefined)markers.push({key:`${o.id}:${name}`,label:name,date,price,tone:"#94a3b8",description:`${name}: ${date}`,showText:false});
  }
  const support=object(o.evidence.support_resistance);const rawZone=object(support.zone);const zones:ChartZoneOverlay[]=[];
  const entries:Record<string,Json>[]=[];
  if(Object.keys(rawZone).length)entries.push(rawZone);
  for(const z of entries) {
    const start=snapshot.bars[0],end=snapshot.bars[snapshot.bars.length-1];if(!start||!end)continue;
    const slope=numeric(z.slope_per_session);const anchor=numeric(z.anchor_session_index);
    const center=numeric(z.anchor_center);const low=numeric(z.anchor_lower);const high=numeric(z.anchor_upper);
    if(slope===null||anchor===null||center===null||low===null||high===null||start.session_index===undefined||end.session_index===undefined)continue;
    const known=String(z.valid_from||z.effective_from||o.session_date);
    const visible=snapshot.bars.filter(b=>b.trade_date>=known);if(!visible.length)continue;
    const first=visible[0];const firstIndex=first.session_index!;const lastIndex=end.session_index;
    zones.push({key:String(z.zone_key),zoneKey:String(z.zone_key),startDate:first.trade_date,endDate:end.trade_date,
      startCenterPrice:center+slope*(firstIndex-anchor),startLowerPrice:low+slope*(firstIndex-anchor),startUpperPrice:high+slope*(firstIndex-anchor),
      endCenterPrice:center+slope*(lastIndex-anchor),endLowerPrice:low+slope*(lastIndex-anchor),endUpperPrice:high+slope*(lastIndex-anchor),
      slopePerSession:slope,slopeAtrPerSession:null,role:z.role==="resistance"?"resistance":"support",description:String(z.zone_key)});
  }
  return {markers,zones};
}
