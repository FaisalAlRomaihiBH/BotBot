# dashboard.py — live localhost dashboard for parallel persona runs.
#
# Run:  python dashboard.py            (serves http://localhost:8500)
# Shows the latest run in parallel_runs/: one card per interview (status,
# turns, score, findings, token usage) plus the streaming run.log. The page
# polls every 2 seconds, so it updates live while a run is going.
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
PORT = 8500

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>RequirementsBot — persona runs</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{padding:14px 22px;background:#171a21;border-bottom:1px solid #262b36;
        display:flex;justify-content:space-between;align-items:baseline}
 h1{font-size:17px;margin:0} #rundir{color:#8a93a6;font-size:13px}
 #cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
        gap:10px;padding:16px 22px}
 .card{background:#171a21;border:1px solid #262b36;border-radius:8px;padding:10px 14px}
 .card b{font-size:13px} .muted{color:#8a93a6;font-size:12px}
 .mini{background:#0a0c10;border:1px solid #232834;border-radius:6px;margin-top:8px;
       padding:8px;font:11px/1.5 Consolas,monospace;white-space:pre-wrap;
       height:150px;overflow-y:auto;color:#9fd0a0}
 .bar{height:6px;background:#262b36;border-radius:3px;margin:8px 0}
 .bar i{display:block;height:6px;border-radius:3px;background:#4f8cff}
 .done .bar i{background:#3ecf6a} .failed .bar i{background:#e5534b}
 .score{float:right;font-weight:700}
 #log{margin:0 22px 22px;background:#0a0c10;border:1px solid #262b36;border-radius:8px;
      padding:12px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;
      max-height:45vh;overflow-y:auto;color:#9fd0a0}
 #summary{margin:0 22px 12px;color:#c9d2e0;font-size:13px}
</style></head><body>
<header><h1>RequirementsBot — parallel persona run</h1><span id="rundir"></span></header>
<div id="cards"></div><div id="summary"></div><pre id="log"></pre>
<script>
async function tick(){
  try{
    const d = await (await fetch('/data')).json();
    document.getElementById('rundir').textContent = d.run_dir || 'no runs yet';
    const stickiness = {};
    document.querySelectorAll('.mini').forEach(m=>{
      stickiness[m.id] = m.scrollTop + m.clientHeight >= m.scrollHeight - 20;
    });
    document.getElementById('cards').innerHTML = d.interviews.map(iv=>{
      const cls = iv.status==='complete'?'done':(iv.status==='failed'?'failed':'');
      const pct = Math.min(100, Math.round(100*iv.turn/30));
      return `<div class="card ${cls}"><b>#${iv.index} ${iv.industry}</b>
        <span class="score">${iv.score??''}</span>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <span class="muted">${iv.status} — turn ${iv.turn}${iv.findings!=null?' · '+iv.findings+' findings':''}
        ${iv.tokens?'<br>'+iv.tokens:''}</span>
        <div class="mini" id="mini${iv.index}">${iv.log.join('\\n')}</div></div>`;
    }).join('');
    document.querySelectorAll('.mini').forEach(m=>{
      if (stickiness[m.id] !== false) m.scrollTop = m.scrollHeight;
    });
    document.getElementById('summary').textContent = d.summary || '';
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
    if run is None:
        return {"run_dir": None, "interviews": [], "log": "", "summary": ""}

    log_file = run / "run.log"
    log_text = log_file.read_text(encoding="utf-8", errors="replace") \
        if log_file.exists() else "(this run has no run.log — older run)"

    # Live per-interview state, reconstructed from the log + result files.
    interviews: dict[int, dict] = {}
    for line in log_text.splitlines():
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
                                         "tokens": None, "log": []})
        # This interview's own line, without the shared [idx industry] prefix.
        iv["log"] = (iv["log"] + [line[line.index("]") + 1:].strip()])[-150:]
        if "] turn " in line:
            iv["turn"] = int(line.split("] turn ")[1].split(":")[0].split("/")[0])
            if "[OWNER LEFT]" in line:
                iv["status"] = "owner left"
        if "] tokens: " in line:
            iv["tokens"] = line.split("] tokens: ")[1]
        if "judging..." in line:
            iv["status"] = "judging"
        if "] score " in line:
            iv["status"] = "complete"
            part = line.split("] score ")[1]          # "6.8/10, 9 findings"
            iv["score"] = part.split(",")[0]
            iv["findings"] = int(part.split(", ")[1].split(" ")[0])
        if "FAILED" in line:
            iv["status"] = "failed"

    summary_file = run / "summary.json"
    summary = ""
    if summary_file.exists():
        s = json.loads(summary_file.read_text(encoding="utf-8"))
        summary = (f"RUN DONE — {s['succeeded']}/{s['count']} judged, "
                   f"{s['completed_interviews']} completed, avg {s['avg_score']}/10")
        if s.get("bot_token_usage"):
            u = s["bot_token_usage"]
            summary += (f" · bot tokens: {u['fresh_in']} fresh, "
                        f"{u['cache_read']} cached, {u['out']} out")

    return {"run_dir": run.name, "log": log_text[-40000:], "summary": summary,
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
