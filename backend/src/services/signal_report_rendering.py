"""Fixed renderers, with one immutable source document for every format."""
import csv
from html import escape
from io import BytesIO, StringIO
import json
import os
from urllib.parse import urlsplit
from src.services.signal_report_assets import encode


def publication_url(identity):
    base=os.getenv("SIGNAL_REPORT_PUBLIC_URL","http://localhost:3000").rstrip("/")
    parsed=urlsplit(base)
    if parsed.scheme not in ("http","https") or not parsed.netloc or parsed.query or parsed.fragment or any(c.isspace() for c in base):raise ValueError("invalid public URL")
    return f"{base}/signals/reports/{identity}"


def report_link(doc):
    return doc["report_url"]


def snapshot_drawing(doc, observation):
    from reportlab.graphics.shapes import Drawing,Line,Rect,String,Circle
    from reportlab.lib.colors import HexColor
    from src.services.signal_report_assets import read_json
    bars=[r for r in read_json(doc["chart_specs"][str(observation["instrument_id"])] )["bars"]
          if r["dt_ny"]<=observation["session_date"]][-120:]
    width,height=450,205; drawing=Drawing(width,height)
    if not bars:return drawing
    left,right,bottom,top=42,440,65,188
    low=min(float(r["low"]) for r in bars);high=max(float(r["high"]) for r in bars)
    span=high-low or high*.01
    y=lambda price:bottom+(float(price)-low)/span*(top-bottom)
    step=(right-left)/len(bars)
    for i in range(5):
        level=low+span*i/4;py=y(level)
        drawing.add(Line(left,py,right,py,strokeColor=HexColor("#e2e8f0"),strokeWidth=.4))
        drawing.add(String(0,py-3,f"{level:.2f}",fontSize=7,fillColor=HexColor("#475569")))
    from reportlab.lib.colors import HexColor
    max_volume=max((float(r["volume"]) for r in bars if r.get("volume") is not None),default=0) or 1
    for i,r in enumerate(bars):
        x=left+(i+.5)*step;color=HexColor("#15803d" if r["close"]>r["open"] else "#be123c" if r["close"]<r["open"] else "#a16207")
        drawing.add(Line(x,y(r["low"]),x,y(r["high"]),strokeColor=color,strokeWidth=.6))
        drawing.add(Rect(x-step*.3,min(y(r["open"]),y(r["close"])),max(.7,step*.6),max(.5,abs(y(r["close"])-y(r["open"]))),strokeColor=color,fillColor=color,strokeWidth=.3))
        if r.get("volume") is not None:drawing.add(Rect(x-step*.3,21,max(.7,step*.6),float(r["volume"])/max_volume*30,strokeColor=None,fillColor=color))
    drawing.add(Circle(right-step*.5,y(bars[-1]["close"]),3,strokeColor=HexColor("#0369a1"),fillColor=None))
    for i in sorted({0,len(bars)//2,len(bars)-1}):
        drawing.add(String(left+(i+.5)*step,7,str(bars[i]["dt_ny"]),fontSize=7,textAnchor="start" if i==0 else "end" if i==len(bars)-1 else "middle"))
    return drawing


def render(doc,fmt,*,summary_only=False):
    if fmt=="json":return encode(doc)
    zh=doc["language"]=="zh-CN"
    if fmt=="csv":
        output=StringIO(newline="");writer=csv.writer(output)
        fields=["id","strategy_name","strategy_type","strategy_version","instrument_id","symbol_as_of","session_date","event_type","event_kind","direction","passes_signal_filters","strategy_rank","strength","evidence"]
        writer.writerow(fields)
        for row in doc["observations"]:
            values=[]
            for key in fields:
                value=row.get(key)
                s=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else "" if value is None else str(value)
                values.append("'"+s if s.lstrip().startswith(("=","+","-","@")) or s.startswith(("\t","\r","\n")) else s)
            writer.writerow(values)
        return output.getvalue().encode("utf-8-sig")
    title=("信号观察报告" if zh else "Signal observation report")+" · "+doc["name"]
    lines=[f"{doc['market']} · {doc['session_date']} · {doc['summary']['status']}",
           ("信号 / 股票：" if zh else "Observations / instruments: ")+f"{doc['summary']['observation_count']} / {doc['summary']['instrument_count']}"]
    retention=("到期时间：" if zh else "Expires: ")+(doc.get("expires_at") or ("等待可信交易日历" if zh else "Waiting for a trusted market calendar") if doc.get("retention_sessions") else ("手动报告，无自动到期" if zh else "Manual report; no automatic expiry"))
    lines.append(retention)
    if fmt=="html":
        details=""
        if not summary_only:
            details="".join("<section><h2>"+escape(o["symbol_as_of"]+" · "+o["strategy_name"]+" · "+o["event_type"])+"</h2><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+escape(json.dumps(o["evidence"],ensure_ascii=False,indent=2))+"</pre></section>" for o in doc["observations"])
        sections="".join(f"<li>{escape(s['name'] or s['strategy_type'])}: {escape(s['status'])} · {escape(str(s['coverage']))}</li>" for s in doc["strategy_sections"])
        return (f'<!doctype html><html lang="{doc["language"]}"><meta charset="utf-8"><title>{escape(title)}</title><body>'
            f'<h1>{escape(title)}</h1>'+"".join(f"<p>{escape(line)}</p>" for line in lines)+f"<ul>{sections}</ul>{details}"
            f'<p><a href="{escape(report_link(doc),quote=True)}">'+("查看报告与 K 线" if zh else "Open report and charts")+"</a></p>"
            f"<p>{'保留交易日数' if zh else 'Retention sessions'}: {doc.get('retention_sessions') or '—'}</p></body></html>").encode()
    if fmt=="pdf":
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle, KeepTogether
        from reportlab.lib.colors import HexColor
        from reportlab.lib.styles import getSampleStyleSheet
        from src.services.report_pdf_font import report_pdf_font
        font,_=report_pdf_font()
        styles=getSampleStyleSheet()
        for style in styles.byName.values():style.fontName=font;style.wordWrap="CJK"
        def fields_table(fields):
            rows=[[Paragraph(escape(str(k)),styles["BodyText"]),Paragraph(escape(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else "—" if v is None else str(v)),styles["BodyText"])] for k,v in fields.items()]
            table=LongTable(rows,colWidths=[155,295],hAlign="LEFT")
            table.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LINEBELOW",(0,0),(-1,-1),.3,HexColor("#e2e8f0")),("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]))
            return table
        stream=BytesIO();story=[Paragraph(escape(title),styles["Title"])]
        story.extend(Paragraph(escape(s),styles["BodyText"]) for s in lines)
        story.append(Paragraph(escape(report_link(doc)),styles["BodyText"]))
        for section in doc["strategy_sections"]:
            heading=Paragraph(escape(section["name"] or section["strategy_type"]),styles["Heading2"])
            coverage=section["coverage"]
            detail=f"{section['status']} · {coverage.get('evaluated',0)}/{coverage.get('expected',0)} · {coverage.get('reasons',{})}"
            story += [Spacer(1,12),KeepTogether([heading,Paragraph(escape(detail),styles["BodyText"])])]
            for o in doc["observations"]:
                if o["strategy_run_id"]!=section["id"]:continue
                story.append(KeepTogether([Paragraph(escape(f"{o['symbol_as_of']} · {o['event_type']} · {o['session_date']}"),styles["Heading3"]),Paragraph(escape(o["evidence"]["reason"]),styles["BodyText"]),snapshot_drawing(doc,o)]))
                story.append(fields_table(o["evidence"].get("observations",{})))
                story.append(Paragraph("策略阈值" if zh else "Strategy thresholds",styles["Heading3"]))
                story.append(fields_table(o["evidence"].get("parameters",{})))
                if o["evidence"].get("setup"):
                    story.append(Paragraph(escape(json.dumps(o["evidence"]["setup"],ensure_ascii=False)),styles["BodyText"]))
        SimpleDocTemplate(stream,title=title).build(story)
        return stream.getvalue()
    raise ValueError("unsupported report format")
