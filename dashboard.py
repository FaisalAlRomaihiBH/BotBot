# dashboard.py — live localhost dashboard for parallel persona runs.
#
# Run:  python dashboard.py            (serves http://localhost:8500)
# Shows the latest run in parallel_runs/ as pipeline paths: every interview is
# a card with milestones Persona -> Interview -> Judge (status, model, costs),
# and one shared card carries the run-level Improve -> Code-fix stages.
# The page polls every 2 seconds, so it updates live while a run is going.
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
PORT = 8500

# "in 45,120 ($0.12) out 3,240 ($0.08) total $0.20" -> stat-box numbers
IO_RE = re.compile(r"in ([\d,]+) \(\$([\d.]+)\) out ([\d,]+) \(\$([\d.]+)\) "
                   r"total \$([\d.]+)")


def parse_io(text: str):
    m = IO_RE.search(text)
    if not m:
        return None
    return {"in_tok": m.group(1), "in_usd": m.group(2),
            "out_tok": m.group(3), "out_usd": m.group(4), "total": m.group(5)}


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>RequirementsBot — persona runs</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:14px 22px;background:#171a21;border-bottom:1px solid #262b36;
        display:flex;justify-content:space-between;align-items:baseline}
 h1{font-size:17px;margin:0} #rundir{color:#8a93a6;font-size:13px}
 #cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));
        gap:10px;padding:16px 22px}
 .card{background:#171a21;border:1px solid #262b36;border-radius:8px;padding:10px 14px}
 .card b{font-size:13px} .muted{color:#8a93a6;font-size:12px}
 .score{float:right;font-weight:700}
 .path{display:flex;align-items:stretch;gap:4px;margin:10px 0}
 .arrow{align-self:center;color:#3a4152;font-size:15px}
 .ms{flex:1;background:#0f1320;border:1px solid #232834;border-radius:8px;
     padding:7px 6px;text-align:center;min-width:0}
 .ms .i{font-size:16px} .ms .t{font-size:11px;font-weight:600;margin-top:1px}
 .ms .m{color:#8a93a6;font-size:10px;margin:1px 0}
 .ms .c{font-size:10px;color:#c9d2e0;margin-top:3px}
 .ms .c b{color:#3ecf6a;font-size:11px}
 .ms .st{font-size:10px;margin-top:3px;color:#8a93a6}
 .ms.done{border-color:#2c5e3f} .ms.done .st{color:#3ecf6a}
 .ms.failed{border-color:#7a2e2a} .ms.failed .st{color:#e5534b}
 .ms.run{border-color:#4f8cff;animation:pulse 1.2s ease-in-out infinite}
 .ms.run .st{color:#4f8cff}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(79,140,255,.35)}
                  50%{box-shadow:0 0 0 5px rgba(79,140,255,0)}}
 .spin{display:inline-block;animation:rot 1s linear infinite}
 @keyframes rot{to{transform:rotate(360deg)}}
 .mini{background:#0a0c10;border:1px solid #232834;border-radius:6px;margin-top:8px;
       padding:8px;font:11px/1.5 Consolas,monospace;white-space:pre-wrap;
       height:130px;overflow-y:auto;color:#9fd0a0}
 #log{margin:0 22px 22px;background:#0a0c10;border:1px solid #262b36;border-radius:8px;
      padding:12px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;
      max-height:40vh;overflow-y:auto;color:#9fd0a0}
</style></head><body>
<header><h1>RequirementsBot — parallel persona run</h1><span id="rundir"></span></header>
<div id="cards"></div><pre id="log"></pre>
<script>
const STATUS_LABEL = {pending:'waiting', run:'', done:'done', failed:'failed'};
function milestone(icon, name, model, status, io, note){
  const st = status==='run' ? '<span class="spin">◌</span> running'
           : (note || STATUS_LABEL[status] || status);
  const costs = io ? `<div class="c">in ${io.in_tok} <b>$${io.in_usd}</b><br>
                      out ${io.out_tok} <b>$${io.out_usd}</b><br>
                      total <b>$${io.total}</b></div>`
                   : (status==='done' ? '' : '');
  return `<div class="ms ${status}"><div class="i">${icon}</div>
    <div class="t">${name}</div>
    <div class="m">${model ? 'Model: '+model.replace('claude-','') : '&nbsp;'}</div>
    ${costs}<div class="st">${st}</div></div>`;
}
const arrow = '<span class="arrow">➜</span>';

async function tick(){
  try{
    const d = await (await fetch('/data')).json();
    document.getElementById('rundir').textContent = d.run_dir || 'no runs yet';
    const stickiness = {};
    document.querySelectorAll('.mini').forEach(m=>{
      stickiness[m.id] = m.scrollTop + m.clientHeight >= m.scrollHeight - 20;
    });

    const n = Math.max(1, d.interviews.length);
    const cards = d.interviews.map(iv=>{
      const g = d.generator || {};
      const gIo = g.io ? {...g.io,
        in_usd:(g.io.in_usd/n).toFixed(2), out_usd:(g.io.out_usd/n).toFixed(2),
        total:(g.io.total/n).toFixed(2), in_tok:g.io.in_tok, out_tok:g.io.out_tok} : null;
      const j = d.judges.find(x=>x.index===iv.index);
      const ivStatus = iv.status==='interviewing' ? 'run'
                     : iv.status==='failed' ? 'failed' : 'done';
      const jStatus = !j ? 'pending' : (j.done ? 'done' : 'run');
      return `<div class="card"><b>#${iv.index} ${iv.industry}</b>
        <span class="score">${iv.score??''}</span>
        <div class="path">
          ${milestone('🎲','Persona', g.model, g.done?'done':'run', gIo,
                      g.done?'done':'generating')}${arrow}
          ${milestone('🤖','Interview', iv.bot_model, ivStatus, iv.io,
                      ivStatus==='done' ? (iv.status==='owner left'?'owner left':'done')
                                        : 'turn '+iv.turn)}${arrow}
          ${milestone('⚖️','Judge', j?j.model:null, jStatus, j?j.io:null,
                      j&&j.done&&iv.findings!=null ? iv.findings+' findings' : null)}
        </div>
        <div class="mini" id="mini${iv.index}">${iv.log.join('\\n')}</div></div>`;
    }).join('');

    const p = d.pipeline;
    const shared = (p.improve.status!=='pending' || p.codefix.status!=='pending' || d.run_finished)
      ? `<div class="card"><b>Run pipeline — after all interviews</b>
         <div class="path">
           ${milestone('🛠️','Improve prompt', p.improve.model, p.improve.status,
                       p.improve.io, p.improve.note)}${arrow}
           ${milestone('🧑‍💻','Code fixes', p.codefix.model, p.codefix.status,
                       p.codefix.io, p.codefix.note)}
         </div></div>` : '';

    document.getElementById('cards').innerHTML = cards + shared;
    document.querySelectorAll('.mini').forEach(m=>{
      if (stickiness[m.id] !== false) m.scrollTop = m.scrollHeight;
    });
    const log = document.getElementById('log');
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
    log.textContent = d.log;
    if (stick) log.scrollTop = log.scrollHeight;
  }catch(e){}
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


def latest_run_dir():
    pointer = RUNS_DIR / "latest.txt"
    if pointer.exists():
        p = Path(pointer.read_text(encoding="utf-8").strip())
        if p.exists():
            return p
    runs = sorted((d for d in RUNS_DIR.iterdir() if d.is_dir()), reverse=True) \
        if RUNS_DIR.exists() else []
    return runs[0] if runs else None


def collect() -> dict:
    run = latest_run_dir()
    empty_stage = lambda: {"status": "pending", "model": None, "io": None, "note": None}
    pipeline = {"improve": empty_stage(), "codefix": empty_stage()}
    if run is None:
        return {"run_dir": None, "interviews": [], "judges": [],
                "generator": None, "pipeline": pipeline,
                "run_finished": False, "log": ""}

    log_file = run / "run.log"
    log_text = log_file.read_text(encoding="utf-8", errors="replace") \
        if log_file.exists() else "(this run has no run.log — older run)"

    interviews: dict[int, dict] = {}
    judges: dict[int, dict] = {}
    generator = {"model": None, "io": None, "done": False}
    run_finished = False

    for line in log_text.splitlines():
        if line.startswith("[personas]"):
            body = line[len("[personas]"):].strip()
            if body.startswith("model "):
                generator["model"] = body[len("model "):]
            elif "generator[" in body:
                generator["io"] = parse_io(body)
                generator["model"] = (generator["model"]
                                      or body.split("generator[")[1].split("]")[0])
                generator["done"] = True
            continue
        if line.startswith("[improve]"):
            body = line[len("[improve]"):].strip()
            st = pipeline["improve"]
            if body.startswith("running with "):
                st.update(status="run", model=body[len("running with "):].rstrip(". "))
            elif "improver[" in body:
                st["io"] = parse_io(body)
            elif body.startswith("prompt updated"):
                st.update(status="done", note=body)
            elif body.startswith("no change"):
                st.update(status="done", note="no change")
            elif "attempt" in body and "rejected" in body:
                st["note"] = body[:60]
            continue
        if line.startswith("[codefix]"):
            body = line[len("[codefix]"):].strip()
            st = pipeline["codefix"]
            if body.startswith("running "):
                st.update(status="run",
                          model=body.split(" with ")[-1].rstrip(". ") if " with " in body else None,
                          note=body.split(" with ")[0].replace("running ", ""))
            elif body.startswith("agent[") and "total $" in body:
                st.update(status="done",
                          io={"in_tok": "—", "in_usd": "0.00", "out_tok": "—",
                              "out_usd": "0.00",
                              "total": body.split("total $")[1].split()[0]})
            elif "claude CLI not found" in body:
                st.update(status="failed", note="claude CLI not found")
            elif "exited" in body:
                st.update(status="failed", note=body[:60])
            elif st["status"] == "run":
                st.update(status="done", note="finished")
            continue
        if line.startswith("=== done"):
            run_finished = True
        if not line.startswith("["):
            continue
        try:
            idx = int(line[1:4])
        except ValueError:
            continue
        industry = line[5:line.index("]")].strip()
        iv = interviews.setdefault(idx, {"index": idx, "industry": industry,
                                         "turn": 0, "status": "interviewing",
                                         "score": None, "findings": None,
                                         "bot_model": None, "persona_model": None,
                                         "io": None, "log": []})
        iv["log"] = (iv["log"] + [line[line.index("]") + 1:].strip()])[-150:]
        if "interviewing... (" in line:
            m = re.search(r"bot: ([\w.-]+), persona: ([\w.-]+)", line)
            if m:
                iv["bot_model"], iv["persona_model"] = m.group(1), m.group(2)
        if "] turn " in line:
            iv["turn"] = int(line.split("] turn ")[1].split(":")[0].split("/")[0])
            if " | in " in line:
                iv["io"] = parse_io(line) or iv.get("io")
            if "[OWNER LEFT]" in line:
                iv["status"] = "owner left"
        if "; judging with " in line:
            if iv["status"] == "interviewing":
                iv["status"] = "interview done"
            judges[idx] = {"index": idx, "done": False, "io": None,
                           "model": line.split("; judging with ")[1].rstrip(". ")}
        if "] judge[" in line:
            body = line.split("] judge[")[1]
            j = judges.setdefault(idx, {"index": idx, "model": "", "io": None})
            j["model"] = body.split("]")[0]
            j["io"] = parse_io(body)
            j["done"] = True
            if iv["status"] != "owner left":
                iv["status"] = "complete"
            if "| score " in body:
                part = body.split("| score ")[1]
                iv["score"] = part.split(",")[0]
                iv["findings"] = int(part.split(", ")[1].split(" ")[0])
        if "FAILED" in line:
            iv["status"] = "failed"

    return {"run_dir": run.name, "log": log_text[-40000:],
            "generator": generator, "pipeline": pipeline,
            "run_finished": run_finished,
            "judges": sorted(judges.values(), key=lambda j: j["index"]),
            "interviews": sorted(interviews.values(), key=lambda i: i["index"])}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            body = json.dumps(collect()).encode("utf-8")
            ctype = "application/json"
        else:
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console quiet
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
