"""Local web UI for deterministic statement extraction plus Ollama agent Q&A."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

from src.agent.derivation import canonicalize_metric
from src.agent.periods import canonicalize_period
from src.agent.runtime import ask as agent_ask, DEFAULT_MODEL
from src.auto_extract import extract_financial_statements
from src.extraction.llm_cleanup import cleanup_table
from src.store.db import LineItem, add_document, add_line_item
from src.store.schema import init_db

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "financials.db"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

HTML = r"""
<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Financial PDF Agent</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f5f6f8;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.app{max-width:1250px;margin:auto;padding:32px 22px 60px}
.top{display:flex;justify-content:space-between;gap:20px;align-items:end}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#6b7280;font-weight:700}
.title{font-size:34px;font-weight:800;margin:4px 0}.sub{color:#6b7280;max-width:760px}
.panel{background:white;border:1px solid #e3e7ee;border-radius:16px;box-shadow:0 10px 30px rgba(15,23,42,.06);padding:22px;margin-top:18px}
.grid{display:grid;grid-template-columns:1fr 180px 1fr;gap:12px}.field{display:flex;flex-direction:column;gap:6px}
.field label{font-size:12px;color:#6b7280;font-weight:700}.field input,.question{border:1px solid #d9dee7;border-radius:10px;padding:11px 12px;font-size:14px}
.button{position:relative;z-index:10;display:inline-flex;align-items:center;justify-content:center;min-height:42px;border:0;border-radius:10px;padding:11px 16px;font-weight:750;background:#111827;color:#fff;cursor:pointer;pointer-events:auto;user-select:none;-webkit-user-select:none}
.button:hover{filter:brightness(1.08)}.button:disabled{opacity:.5;cursor:wait}
.status{margin-top:12px;color:#6b7280}.bar{height:4px;background:#e9edf2;border-radius:9px;margin-top:10px;overflow:hidden;display:none}
.bar i{display:block;width:35%;height:100%;background:#111827;animation:slide 1s infinite}@keyframes slide{from{margin-left:-40%}to{margin-left:110%}}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:18px}.card{padding:15px;border:1px solid #e7ebf0;border-radius:12px}
.label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#6b7280;font-weight:700}.value{font-size:25px;font-weight:800;margin-top:5px}
.tabs{display:flex;gap:6px;margin-bottom:14px}.tab{flex:1;border:0;padding:10px;border-radius:9px;background:#eef1f5;color:#6b7280;font-weight:750;cursor:pointer}.tab.active{background:#fff;box-shadow:0 2px 8px rgba(15,23,42,.08);color:#111827}
.statement{display:none}.statement.active{display:block}.candidate{border:1px solid #e3e7ee;border-radius:12px;margin:9px 0;overflow:hidden}.candidate summary{padding:13px 15px;cursor:pointer;display:flex;justify-content:space-between;gap:15px;list-style:none}.candidate summary::-webkit-details-marker{display:none}
.body{padding:0 15px 15px;border-top:1px solid #e3e7ee}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}.chip{font-size:11px;padding:4px 7px;background:#f0f2f5;border-radius:999px;color:#5f6876}.chip.good{background:#e8f7ee}.chip.warn{background:#fff3df}.chip.bad{background:#fdecea}
.pre{white-space:pre-wrap;color:#6b7280;font-size:12px;line-height:1.5;margin-top:10px}.qa{display:grid;grid-template-columns:1fr auto;gap:10px}.answer{white-space:pre-wrap;line-height:1.6;font-size:14px;margin-top:15px;border-top:1px solid #e7ebf0;padding-top:15px}.hidden{display:none}
@media(max-width:800px){.grid,.qa{grid-template-columns:1fr}.summary{grid-template-columns:1fr 1fr}.top{flex-direction:column;align-items:flex-start}}
</style></head>
<body><div class="app">
<div class="top"><div><div class="eyebrow">Financial PDF Agent</div><div class="title">Evidence-first financial analysis</div><div class="sub">Upload a filing, verify the three statements deterministically, then ask questions against the stored evidence using the local Mistral agent.</div></div></div>
<div class="panel"><div class="grid"><div class="field"><label>Company / entity</label><input id="entity" placeholder="HDFC Bank"></div><div class="field"><label>Period</label><input id="period" value="FY2025"></div><div class="field"><label>PDF</label><input id="file" type="file" accept="application/pdf"></div></div><div style="margin-top:14px;position:relative;z-index:20"><button type="button" class="button" id="analyze">Analyze filing</button></div><div class="status" id="status"></div><div class="bar" id="bar"><i></i></div></div>
<div id="results" class="hidden"></div>
<div class="panel hidden" id="qaPanel"><div class="eyebrow">Agent</div><div style="font-size:22px;font-weight:800;margin:4px 0 14px">Ask the filing</div><div class="qa"><input class="question" id="question" placeholder="What was total debt in FY2025?"><button type="button" class="button" id="ask">Ask</button></div><div class="status" id="qaStatus"></div><div class="answer" id="answer"></div></div>
</div>
<script>
let context={entity:null};
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
function quality(q){return q>=.78?'good':q>=.5?'warn':'bad'}
function tableHtml(t){const rows=t?.rows||[];if(!rows.length)return'';return '<div style="overflow:auto;margin-top:12px"><table style="width:100%;border-collapse:collapse;font-size:12px">'+rows.map((r,i)=>'<tr>'+r.map((x,j)=><'+(i===0?'th':'td')+' style="padding:7px;border-bottom:1px solid #edf0f4;text-align:'+(j?'right':'left')+';font-weight:'+(i===0?'700':'400')+'">'+esc(x)+'</'+(i===0?'th':'td')+'>').join('')+'</tr>').join('')+'</table></div>'}
function render(data){const names={balance_sheet:'Balance Sheet',income_statement:'Income Statement',cash_flow:'Cash Flow'};let totalTables=0,warns=0,confirmed=0;Object.values(data.statements||{}).forEach(ps=>(ps||[]).forEach(p=>(p.tables||[]).forEach(t=>{totalTables++;warns+=(t.warnings||[]).length})));Object.values(data.statements||{}).forEach(ps=>(ps||[]).forEach(p=>{if(p.status==='CONFIRMED')confirmed++}));let h='<div class="summary"><div class="card"><div class="label">Statements confirmed</div><div class="value">'+confirmed+'/3</div></div><div class="card"><div class="label">Tables</div><div class="value">'+totalTables+'</div></div><div class="card"><div class="label">Warnings</div><div class="value">'+warns+'</div></div><div class="card"><div class="label">Agent</div><div class="value" style="font-size:18px">Mistral 24B</div></div></div><div class="panel"><div class="tabs">'+Object.entries(names).map(([k,n],i)=>'<button type="button" class="tab '+(i===0?'active':'')+'" data-tab="'+k+'">'+n+'</button>').join('')+'</div>';
Object.entries(names).forEach(([k,n],i)=>{h+='<section class="statement '+(i===0?'active':'')+'" data-sec="'+k+'">';const pages=data.statements?.[k]||[];if(!pages.length){h+='<div class="muted">No title-matched candidate pages.</div></section>';return;}pages.forEach((p,idx)=>{const t=(p.tables||[]).slice().sort((a,b)=>(b.quality_score??0)-(a.quality_score??0))[0];const q=t?(t.quality_score??t.confidence??0):0;h+='<details class="candidate" '+(idx===0?'open':'')+'><summary><div><b>Page '+p.page+'</b><div class="muted">'+esc(p.status||'UNKNOWN')+' · discovery '+esc(p.score)+'</div></div><div class="chips"><span class="chip '+(p.status==='CONFIRMED'?'good':'warn')+'">'+esc(p.status)+'</span>'+ (t?'<span class="chip '+quality(q)+'">'+Math.round(q*100)+'% extraction</span>':'') +'</div></summary><div class="body"><div class="chips"><span class="chip">Title: '+esc(p.title_match||'none')+'</span><span class="chip">Gate 2 labels: '+esc(p.gate2_label_hits)+'</span><span class="chip">Gate 3 numeric cols: '+esc(p.gate3_numeric_columns)+'</span>'+(p.review_flag?'<span class="chip warn">'+esc(p.review_flag)+'</span>':'')+'</div><div class="pre">'+esc(p.text_preview||'')+'</div>'+tableHtml(t)+'</div></details>'});h+='</section>'});h+='</div>'; $('results').innerHTML=h;$('results').classList.remove('hidden');document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.statement').forEach(s=>s.classList.toggle('active',s.dataset.sec===b.dataset.tab))});$('qaPanel').classList.remove('hidden')}
$('file').addEventListener('change',()=>{const f=$('file').files[0];$('status').textContent=f?`Selected: ${f.name}`:'No PDF selected.'});
$('analyze').addEventListener('click',async()=>{const f=$('file').files[0],entity=$('entity').value.trim(),period=$('period').value.trim();if(!f||!entity||!period){$('status').textContent=!f?'Please choose a PDF first.':'Enter company, period and PDF.';return;}$('analyze').disabled=true;$('status').textContent='Discovering statements, validating structure and storing evidence…';$('bar').style.display='block';const fd=new FormData();fd.append('file',f);fd.append('entity',entity);fd.append('period',period);try{const r=await fetch('/extract',{method:'POST',body:fd});const data=await r.json();if(!r.ok)throw new Error(data.error||'Extraction failed');context={entity};render(data);$('status').textContent='Analysis complete. Evidence stored for agent queries.'}catch(e){$('status').textContent='Error: '+e.message}finally{$('analyze').disabled=false;$('bar').style.display='none'}});
$('ask').addEventListener('click',async()=>{const q=$('question').value.trim();if(!q||!context.entity)return;$('ask').disabled=true;$('qaStatus').textContent='Agent is retrieving evidence and calculating where required…';$('answer').textContent='';try{const r=await fetch('/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,entity:context.entity})});const data=await r.json();if(!r.ok)throw new Error(data.error||'Agent failed');$('answer').textContent=data.answer||'';$('qaStatus').textContent='Agent response complete.'}catch(e){$('qaStatus').textContent='Error: '+e.message}finally{$('ask').disabled=false}});
</script></body></html>
"""


def _best_table(page: dict) -> dict | None:
    tables = page.get("tables") or []
    return max(tables, key=lambda t: (t.get("quality_score", 0.0), t.get("confidence", 0.0))) if tables else None


def _persist_confirmed(result: dict, entity: str, period: str, source_path: str) -> dict:
    conn = init_db(str(DB_PATH))
    requested_period = canonicalize_period(period)
    document_id = add_document(conn, entity, "annual_report", requested_period, source_path)
    stored = 0
    skipped = 0
    for statement, pages in (result.get("statements") or {}).items():
        for page in pages or []:
            if page.get("status") != "CONFIRMED":
                continue
            table = _best_table(page)
            if not table:
                continue
            try:
                cleaned = cleanup_table(table.get("rows") or [], model=DEFAULT_MODEL)
            except Exception:
                cleaned = []
            for row in cleaned:
                if row.get("ambiguous_multi_period"):
                    skipped += 1
                    continue
                metric_raw = str(row.get("metric_raw") or "").strip()
                value = row.get("value")
                if not metric_raw or value is None:
                    skipped += 1
                    continue
                row_period = canonicalize_period(row.get("period_raw")) if row.get("period_raw") else requested_period
                add_line_item(conn, document_id, LineItem(
                    entity=entity, period=row_period, statement=statement,
                    metric=canonicalize_metric(metric_raw), metric_raw=metric_raw,
                    value=value, unit=row.get("unit") or "unspecified", consolidated=None,
                    source_page=page.get("page"), source_table=table.get("table_caption"),
                    extraction_method=table.get("method") or "unknown",
                    extraction_confidence=table.get("confidence"),
                ))
                stored += 1
    conn.close()
    return {"document_id": document_id, "stored_line_items": stored, "skipped_rows": skipped}


@app.get("/")
def index():
    return render_template_string(HTML)


@app.post("/extract")
def extract():
    upload = request.files.get("file")
    entity = request.form.get("entity", "").strip()
    period = request.form.get("period", "").strip()
    if upload is None or not upload.filename.lower().endswith(".pdf"):
        return jsonify(error="Please upload a PDF file."), 400
    if not entity or not period:
        return jsonify(error="Company/entity and period are required."), 400

    safe_name = f"{uuid.uuid4().hex}_{Path(upload.filename).name}"
    saved_path = UPLOAD_DIR / safe_name
    upload.save(saved_path)
    try:
        result = extract_financial_statements(str(saved_path))
        result["storage"] = _persist_confirmed(result, entity, period, str(saved_path))
        result["entity"] = entity
        result["requested_period"] = canonicalize_period(period)
        return jsonify(result)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.post("/ask")
def ask():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    entity = str(payload.get("entity") or "").strip()
    db_path = str(DB_PATH)
    if not question or not entity:
        return jsonify(error="question and entity are required"), 400
    try:
        answer = agent_ask(question, entity=entity, db_path=db_path, model=DEFAULT_MODEL)
        return jsonify({"answer": answer, "entity": entity})
    except Exception as exc:
        return jsonify(error=str(exc)), 500


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local financial PDF Agent UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=not args.no_debug)


if __name__ == "__main__":
    main()
