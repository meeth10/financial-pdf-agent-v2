"""Evidence-first analyst UI: extract a PDF, select evidence, ask Mistral Small 3.2."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, jsonify, render_template_string, request

from src.auto_extract import extract_financial_statements
from src.auto_ingest import ingest_pages_into_store
from src.store.schema import init_db
from src.store.db import add_document
from src.agent.run import ask as agent_ask
from src.agent.run import DEFAULT_MODEL

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Financial Analyst Workbench</title>
<style>
:root{--bg:#f4f5f7;--panel:#fff;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--dark:#111827;--good:#067647;--warn:#a15c00;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif}.app{max-width:1450px;margin:auto;padding:28px 24px 60px}.top{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:24px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:800;color:var(--muted)}h1{margin:5px 0;font-size:34px}.sub{color:var(--muted);max-width:820px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:0 8px 28px rgba(15,23,42,.06)}.upload{padding:22px;margin-bottom:18px;display:flex;justify-content:space-between;gap:18px;align-items:center}.button{border:0;border-radius:10px;padding:11px 16px;font-weight:750;background:var(--dark);color:#fff;cursor:pointer}.button.alt{background:#eef1f5;color:var(--dark)}input[type=file]{display:none}.drop{display:flex;gap:14px;align-items:center;cursor:pointer}.pdf{width:44px;height:44px;border-radius:11px;background:var(--dark);color:#fff;display:grid;place-items:center;font-size:12px;font-weight:800}.hint{font-size:12px;color:var(--muted);margin-top:3px}.layout{display:grid;grid-template-columns:1.65fr .85fr;gap:18px}.main,.side{padding:18px}.tabs{display:flex;gap:7px;background:#eef1f5;padding:7px;border-radius:12px;margin-bottom:15px}.tab{flex:1;border:0;background:transparent;border-radius:8px;padding:11px;font-weight:700;color:var(--muted);cursor:pointer}.tab.active{background:#fff;color:var(--ink);box-shadow:0 2px 7px rgba(0,0,0,.08)}.page{border:1px solid var(--line);border-radius:12px;margin-bottom:10px;overflow:hidden}.page-head{display:flex;justify-content:space-between;gap:15px;padding:12px 14px}.page-body{border-top:1px solid var(--line);padding:13px}.select{display:flex;gap:8px;align-items:center}.badge{display:inline-flex;padding:4px 7px;border-radius:999px;background:#f0f2f5;font-size:11px;color:#5d6674;margin:2px}.good{background:#eaf7ef;color:var(--good)}.warn{background:#fff4df;color:var(--warn)}.bad{background:#fdecea;color:var(--bad)}.tablewrap{overflow:auto;border:1px solid var(--line);border-radius:10px;margin-top:10px}.data{border-collapse:collapse;width:100%;min-width:620px}.data th,.data td{padding:9px 10px;border-bottom:1px solid #edf0f3;font-size:12px;text-align:right}.data th:first-child,.data td:first-child{text-align:left;position:sticky;left:0;background:#fff}.data th{font-size:10px;text-transform:uppercase;color:var(--muted);background:#fafbfc}.question{width:100%;min-height:110px;border:1px solid var(--line);border-radius:11px;padding:12px;font:inherit;resize:vertical}.result{margin-top:15px;padding:16px;border-radius:13px;background:#111827;color:#f9fafb;min-height:100px;white-space:pre-wrap;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}.result strong{font-family:inherit}.empty{padding:30px;text-align:center;color:var(--muted)}#status{font-size:13px;color:var(--muted)}@media(max-width:1000px){.layout{grid-template-columns:1fr}.upload,.top{align-items:flex-start;flex-direction:column}.button{width:100%}}
</style></head>
<body><div class="app">
<div class="top"><div><div class="eyebrow">Financial PDF Agent · Evidence-first</div><h1>Analyst Workbench</h1><div class="sub">Extract the filing first. Select the exact pages/tables you want the agent to inspect. Then ask a financial question. Mistral Small 3.2 sees only the evidence you selected.</div></div><div id="status">Ready</div></div>
<div class="panel upload"><label class="drop" for="file"><div class="pdf">PDF</div><div><strong id="filename">Choose an annual report</strong><div class="hint">The extractor scans the whole document automatically.</div></div></label><input id="file" type="file" accept="application/pdf"><button class="button" id="extract">Extract statements</button></div>
<div class="layout">
<div class="panel main"><div class="tabs"><button class="tab active" data-tab="balance_sheet">Balance Sheet</button><button class="tab" data-tab="income_statement">Income Statement</button><button class="tab" data-tab="cash_flow">Cash Flow</button></div><div id="pages"><div class="empty">Upload a PDF to see extracted evidence.</div></div></div>
<div class="panel side"><div class="eyebrow">Ask the analyst</div><h2 style="margin:6px 0 4px">Question</h2><div class="hint" style="margin-bottom:10px">Select one or more evidence blocks on the left.</div><textarea id="question" class="question" placeholder="e.g. What is EBITDA for FY2025?&#10;Calculate EBITDA margin.&#10;What changed in operating cash flow?"></textarea><button class="button" style="width:100%;margin-top:10px" id="ask" disabled>Ask Mistral 3.2</button><div id="answer" class="result">No analysis yet.</div></div>
</div></div>
<script>
let data=null, active='balance_sheet';const fileEl=document.getElementById('file'),nameEl=document.getElementById('filename'),statusEl=document.getElementById('status'),pagesEl=document.getElementById('pages'),ask=document.getElementById('ask'),answer=document.getElementById('answer');
fileEl.onchange=()=>nameEl.textContent=fileEl.files[0]?.name||'Choose an annual report';document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');active=b.dataset.tab;renderPages()});
function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
function bestTable(p){return (p.tables||[]).slice().sort((a,b)=>(b.quality_score??b.confidence??0)-(a.quality_score??a.confidence??0))[0]}
function tableHtml(t){let rows=t?.rows||[];if(!rows.length)return '<div class="empty">No validated table.</div>';let w=Math.max(...rows.map(r=>r.length));let rs=rows.map(r=>Array.from({length:w},(_,i)=>r[i]??''));let h=rs[0];let body=rs.slice(1);return '<div class="tablewrap"><table class="data"><thead><tr>'+h.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+body.map(r=>'<tr>'+r.map((x,i)=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>'}
function renderPages(){if(!data)return;const pages=(data.statements?.[active]||[]);if(!pages.length){pagesEl.innerHTML='<div class="empty">No candidate pages found.</div>';return}pagesEl.innerHTML=pages.slice(0,5).map((p,i)=>{let t=bestTable(p),q=t?(t.quality_score??t.confidence??0):0;return `<div class="page"><div class="page-head"><div class="select"><input type="checkbox" class="evidence" data-page="${p.page}" ${i===0?'checked':''}><div><strong>Page ${p.page}</strong><div class="hint">Discovery score ${p.score} · ${p.needs_ocr?'OCR likely':'text layer'}</div></div></div><div><span class="badge ${q>=.78?'good':q>=.5?'warn':'bad'}">${t?Math.round(q*100)+'% quality':'No table'}</span></div></div><div class="page-body"><div>${(p.matched_terms||[]).map(x=>'<span class="badge">'+esc(x)+'</span>').join('')}${(t?.warnings||[]).map(x=>'<span class="badge warn">'+esc(x)+'</span>').join('')}</div>${t?tableHtml(t):''}</div></div>`}).join('');ask.disabled=false}
document.getElementById('extract').onclick=async()=>{let f=fileEl.files[0];if(!f)return;statusEl.textContent='Extracting entire filing…';document.getElementById('extract').disabled=true;try{let fd=new FormData();fd.append('file',f);let r=await fetch('/extract',{method:'POST',body:fd});data=await r.json();if(!r.ok)throw Error(data.error||'Extraction failed');renderPages();statusEl.textContent='Extraction complete — select evidence and ask.'}catch(e){statusEl.textContent='Error: '+e.message}finally{document.getElementById('extract').disabled=false}};
ask.onclick=async()=>{let q=document.getElementById('question').value.trim();if(!q||!data)return;let selected=[...document.querySelectorAll('.evidence:checked')].map(x=>Number(x.dataset.page));let pages=(data.statements?.[active]||[]).filter(p=>selected.includes(p.page));if(!pages.length){answer.textContent='Select at least one evidence page.';return}ask.disabled=true;answer.textContent='Mistral is reviewing the selected evidence…';try{let r=await fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q,statement:active,evidence:{pdf:data.pdf,statement:active,pages}})});let out=await r.json();if(!r.ok){
  const summary=out.ingest_summary;
  if(summary){
    const lines=[out.answer||out.error||'Analysis failed.','',`line_items_stored: ${summary.line_items_stored ?? 0}`,`pages_used: ${summary.pages_used ?? 0}`,`skipped_low_quality_tables: ${summary.skipped_low_quality_tables ?? 0}`,`skipped_ambiguous_multi_period_rows: ${summary.skipped_ambiguous_multi_period_rows ?? 0}`,`skipped_unparsed_rows: ${summary.skipped_unparsed_rows ?? 0}`];
    const errors=summary.cleanup_errors||[];
    if(errors.length){lines.push('', 'cleanup_errors:');errors.forEach(e=>lines.push(`page ${e.page}: ${e.error}`));}
    throw Error(lines.join('\n'));
  }
  throw Error(out.error||'Analysis failed');
}answer.textContent=out.answer||JSON.stringify(out,null,2)}catch(e){answer.textContent='Error: '+e.message}finally{ask.disabled=false}};
</script></body></html>
"""


@app.get("/")
def index():
    return render_template_string(HTML)


@app.post("/extract")
def extract():
    upload = request.files.get("file")
    if upload is None or not upload.filename.lower().endswith(".pdf"):
        return jsonify(error="Please upload a PDF file."), 400
    with tempfile.TemporaryDirectory(prefix="financial_pdf_") as tmp:
        path = Path(tmp) / Path(upload.filename).name
        upload.save(path)
        try:
            return jsonify(extract_financial_statements(str(path)))
        except Exception as exc:
            return jsonify(error=str(exc)), 500


@app.post("/analyze")
def analyze():
    """Ingest selected evidence into a request-scoped store, then answer through
    the same deterministic tool-calling agent used by the CLI.

    Ingestion diagnostics are preserved and returned when the selected evidence
    yields no line items, so model/table cleanup failures are visible instead of
    collapsing into a generic "try another page" message.
    """
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    evidence = payload.get("evidence") or {}
    if not question:
        return jsonify(error="Enter a question."), 400

    statement = evidence.get("statement")
    pages = evidence.get("pages") or []
    if not statement or not pages:
        return jsonify(error="Select at least one evidence page."), 400

    entity = Path(str(evidence.get("pdf") or "uploaded_filing")).stem or "uploaded_filing"

    try:
        conn = init_db(":memory:")
        document_id = add_document(conn, entity, "annual_report", "unknown", str(evidence.get("pdf") or ""))
        ingest_summary = ingest_pages_into_store(
            conn, document_id, entity=entity, period="UNKNOWN", statement=statement,
            pages=pages, model=DEFAULT_MODEL,
        )
        if ingest_summary["line_items_stored"] == 0:
            diagnostics = ingest_summary.get("cleanup_errors") or []
            if diagnostics:
                detail = "; ".join(f"page {d.get('page')}: {d.get('error')}" for d in diagnostics)
                message = f"Evidence ingestion failed during table cleanup. {detail}"
            else:
                message = (
                    "The selected evidence yielded no line items this agent could confidently store: "
                    "either the table quality was too low, or numeric values were ambiguous with no "
                    "year header to resolve them against."
                )
            return jsonify(answer=message, ingest_summary=ingest_summary), 422
        answer_text = agent_ask(conn, question, model=DEFAULT_MODEL, entity=entity)
        return jsonify(answer=answer_text, ingest_summary=ingest_summary)
    except Exception as exc:
        return jsonify(error=f"Analyst failed: {exc}"), 500


def main() -> None:
    p = argparse.ArgumentParser(description="Run evidence-first financial analyst UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5002)
    p.add_argument("--no-debug", action="store_true")
    args = p.parse_args()
    app.run(host=args.host, port=args.port, debug=not args.no_debug)


if __name__ == "__main__":
    main()
