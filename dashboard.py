# dashboard.py — BotBot server: owner operations console + client intake chat.
#
# Two entry points, one backend, one RequirementsBot engine:
#   /admin  (also /)  the PLATFORM OWNER's operations console: system-wide
#                     orchestrator map, sessions inspection, contracts &
#                     review, floating AI supervisor (system scope).
#   /chat             a CLIENT-facing page: only the RequirementsBot
#                     conversation. No sidebar, no graph, no costs, no
#                     supervisor, no other clients' records.
#
# Access model (V1, localhost only — see release blockers in the repo docs):
#   - Owner: an unguessable owner token is stored server-side and set as an
#     HttpOnly cookie when /admin is served from localhost. Every /api/* and
#     legacy owner route requires it. This gates client sessions out of the
#     console; it is NOT internet-grade auth — do not expose publicly.
#   - Client: the first message POSTed from /chat creates one isolated
#     project and a server-issued session token (HttpOnly cookie). A client
#     request is authorized ONLY by that cookie — client-supplied project
#     ids, query params and owner cookies are never honored on client routes.
#   - Mutating POSTs verify the Origin header against this host.
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(__import__("os").environ.get("PORT", 8500))

import api_gate  # noqa: E402
from orchestrator import controller, registry, store, supervisor  # noqa: E402

OWNER_TOKEN_FILE = store.DATA_DIR / "owner_token.txt"


def _owner_token() -> str:
    store.DATA_DIR.mkdir(exist_ok=True)
    if not OWNER_TOKEN_FILE.exists():
        OWNER_TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return OWNER_TOKEN_FILE.read_text(encoding="utf-8").strip()


# ============================ OWNER CONSOLE PAGE ============================
ADMIN_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BotBot Orchestrator — Console</title>
<style>
:root{
  --bg:#0d0d0d; --panel:#151515; --panel2:#1a1a1a; --border:#2a2a2a;
  --border-hi:#3f3f46; --text:#f5f5f5; --text2:#a1a1aa; --muted:#71717a;
  --accent:#6ea8fe; --green:#4ade80; --red:#f87171; --amber:#fbbf24;
  --mono:'Cascadia Code',Consolas,'SF Mono',monospace;
  --sans:-apple-system,'Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 var(--sans)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:#333;border-radius:4px}
::-webkit-scrollbar-track{background:transparent}

/* ---------- app shell ---------- */
#shell{display:flex;height:100vh;overflow:hidden}
#sidebar{width:280px;flex:none;background:var(--panel);border-right:1px solid var(--border);
  display:flex;flex-direction:column;transition:width .15s ease;overflow:hidden}
#sidebar.collapsed{width:44px}
#sb-head{display:flex;align-items:center;gap:10px;padding:14px 14px;border-bottom:1px solid var(--border)}
#sb-logo{width:26px;height:26px;flex:none;border:1px solid var(--border-hi);border-radius:6px;
  display:grid;place-items:center;font:600 12px var(--mono);color:var(--accent)}
#sb-title{font-weight:700;font-size:20px;white-space:nowrap;letter-spacing:-.01em}
#sb-toggle{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:13px;padding:2px 4px;border-radius:4px}
#sb-toggle:hover{color:var(--text);background:var(--panel2)}
#sb-nav{padding:8px 6px;flex:1;overflow-y:auto}
.nav-item{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:5px;
  color:var(--text2);cursor:pointer;white-space:nowrap;font-size:12.5px}
.nav-item:hover{background:var(--panel2);color:var(--text)}
.nav-item.active{background:var(--panel2);color:var(--text)}
.nav-item.active .nav-ico{color:var(--accent)}
.nav-ico{width:16px;text-align:center;flex:none;font-size:12px;color:var(--muted)}
#sb-foot{border-top:1px solid var(--border);padding:10px 12px;font:11px var(--mono);
  color:var(--muted);white-space:nowrap}
#sb-foot .dot{display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--muted);margin-right:6px;vertical-align:1px}
#sb-foot .dot.ok{background:var(--green)}
#sb-foot .dot.bad{background:var(--red)}
#sb-foot .dot.warn{background:var(--amber)}
#rl-bars{margin-top:8px;display:flex;flex-direction:column;gap:6px}
.rl-row{display:flex;flex-direction:column;gap:3px}
.rl-top{display:flex;justify-content:space-between;align-items:baseline}
.rl-top em{font:600 8.5px var(--sans);font-style:normal;
  text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.rl-top b{font:9.5px var(--mono);color:var(--text2);font-weight:500;
  white-space:nowrap}
/* fuel gauge: the FILLED part is what REMAINS — a full green bar means
   the whole minute's budget is still available */
.rl-bar{height:5px;border-radius:99px;background:#0b0b0b;
  border:1px solid var(--border);overflow:hidden}
.rl-bar span{display:block;height:100%;border-radius:99px}
#sidebar.collapsed #rl-bars{display:none}
#sb-foot div{margin:3px 0}
#sidebar.collapsed #sb-title,#sidebar.collapsed .nav-label,#sidebar.collapsed #sb-foot{display:none}

#main{flex:1;display:flex;flex-direction:column;min-width:0;overflow-y:auto}

/* ---------- header ---------- */
#run-header{display:flex;align-items:center;gap:14px;padding:12px 20px;
  border-bottom:1px solid var(--border);background:var(--panel);position:sticky;top:0;z-index:5}
#run-title{font-size:14px;font-weight:600}
#run-actions{margin-left:auto;display:flex;gap:6px;align-items:center}
.act{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer}
.act:hover{border-color:var(--border-hi);color:var(--text)}
.act:disabled{opacity:.5;cursor:default}

/* ---------- status badges ---------- */
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:99px;
  font:600 11px var(--sans);border:1px solid var(--border)}
.badge .b-dot{width:6px;height:6px;border-radius:50%;flex:none}
.badge.running{color:var(--accent);border-color:#28405f;background:rgba(110,168,254,.07)}
.badge.running .b-dot{background:var(--accent);animation:pulse 1.6s ease-in-out infinite}
.badge.completed{color:var(--green);border-color:#234534;background:rgba(74,222,128,.06)}
.badge.completed .b-dot{background:var(--green)}
.badge.failed{color:var(--red);border-color:#552b2b;background:rgba(248,113,113,.06)}
.badge.failed .b-dot{background:var(--red)}
.badge.idle{color:var(--muted)}
.badge.idle .b-dot{background:var(--muted)}
.badge.retrying{color:var(--amber);border-color:#5c4a1e}
.badge.retrying .b-dot{background:var(--amber)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.spin{display:inline-block;width:10px;height:10px;border:1.5px solid var(--border-hi);
  border-top-color:var(--accent);border-radius:50%;animation:rot .8s linear infinite;flex:none}
@keyframes rot{to{transform:rotate(360deg)}}

/* ---------- views ---------- */
#chat,#overview,#pipelines,#activity,#customers,#sim,#ratelimits,#billing{display:none}
body.view-experiments #sim,body.view-testsuites #sim{display:flex}
body.view-experiments #main,body.view-testsuites #main{overflow:hidden}
body.view-chat #chat{display:flex}
body.view-overview #overview{display:flex}
body.view-pipelines #pipelines{display:flex}
body.view-activity #activity{display:flex}
body.view-customers #customers{display:flex}
body.view-ratelimits #ratelimits{display:flex}
body.view-billing #billing{display:flex}

/* ---------- rate limiting + costs tabs ---------- */
#ratelimits,#billing{flex:1;flex-direction:column;margin:14px 20px 20px;
  min-height:0;overflow-y:auto;gap:12px}
.qc-cards{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(170px,1fr));gap:10px}
.qc-card{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:12px 14px}
.qc-card .v{font:600 20px var(--mono);color:var(--text)}
.qc-card .l{font:10.5px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);margin-top:3px}
.qc-card.warn .v{color:var(--amber)}
.qc-banner{background:rgba(251,191,36,.08);border:1px solid #5c4a1e;
  border-radius:8px;padding:10px 14px;color:var(--amber);
  font:12.5px var(--sans)}
.qc-sec{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;overflow:hidden}
.qc-sec .h{font:600 10.5px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);padding:9px 12px;
  border-bottom:1px solid var(--border)}
.qc-sec table{width:100%;border-collapse:collapse;font-size:12px}
.qc-sec th{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);text-align:left;
  padding:8px 12px;border-bottom:1px solid var(--border)}
.qc-sec td{padding:7px 12px;border-bottom:1px solid var(--border);
  color:var(--text2)}
.qc-sec td.mono,.qc-sec th.r{font:11px var(--mono);white-space:nowrap}
.qc-sec td.r,.qc-sec th.r{text-align:right}
.qc-st{font:600 10.5px var(--mono)}
.qc-st.running{color:var(--accent)}
.qc-st.queued{color:var(--muted)}
.qc-st.waiting_capacity{color:var(--amber)}
.qc-st.done{color:var(--green)}
.qc-st.failed{color:var(--red)}
.qc-pri{font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.05em;border-radius:99px;padding:1px 7px}
.qc-pri.interactive{color:var(--accent);border:1px solid #28405f}
.qc-pri.background{color:var(--muted);border:1px solid var(--border)}

/* feature-check tick chips */
#simt-featchips{display:flex;flex-direction:column;gap:10px;
  max-height:340px;overflow-y:auto;background:var(--panel2);
  border:1px solid var(--border);border-radius:6px;padding:10px 12px}
.fgroup .fg-t{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);margin-bottom:6px}
.fgroup .fg-c{display:flex;flex-wrap:wrap;gap:6px}
.fchip{font:11.5px var(--sans);color:var(--text2);background:var(--panel);
  border:1px solid var(--border);border-radius:99px;padding:3px 11px;
  cursor:pointer;user-select:none}
.fchip:hover{border-color:var(--border-hi)}
.fchip.on{color:var(--accent);border-color:var(--accent);
  background:rgba(110,168,254,.08)}
.fchip.on::before{content:"✓ ";font-weight:600}

/* ---------- simulation lab ---------- */
#sim{flex:1;flex-direction:column;margin:14px 20px 20px;min-height:0;gap:12px}
.sim-head{display:flex;align-items:center;gap:10px}
.sim-head .t{font:600 13px var(--sans);color:var(--text)}
.sim-head .m{font:11px var(--sans);color:var(--muted)}
#sim-form{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:14px;display:none;flex-direction:column;gap:9px}
#sim-form.open{display:flex}
#sim-form label{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted)}
#sim-form input,#sim-form select,#sim-form textarea{background:var(--panel2);
  border:1px solid var(--border);color:var(--text);border-radius:6px;
  padding:7px 10px;font:12.5px var(--sans)}
#sim-form textarea{min-height:130px;font:12px var(--mono);resize:vertical}
#sim-form .row{display:flex;gap:8px}
#sim-personas{display:grid;grid-template-columns:repeat(auto-fill,
  minmax(250px,1fr));gap:10px}
.sim-card{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:7px}
.sim-card .n{font:600 13.5px var(--sans);color:var(--text);display:flex;
  align-items:center;gap:8px}
.sim-card .k{font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.06em;color:var(--accent);border:1px solid #28405f;
  border-radius:99px;padding:1px 8px}
.sim-card .p{font:11px var(--sans);color:var(--muted);line-height:1.5;
  max-height:48px;overflow:hidden}
.sim-card .b{display:flex;gap:6px;align-items:center;margin-top:2px}
.sim-card .rc{font:10.5px var(--mono);color:var(--muted);margin-left:auto}
#sim-runs{flex:1;min-height:0;overflow-y:auto;background:var(--panel);
  border:1px solid var(--border);border-radius:8px}
#sim-runs table{width:100%;border-collapse:collapse;font-size:12px}
#sim-runs th{position:sticky;top:0;background:var(--panel);z-index:1;
  font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);text-align:left;padding:9px 12px;
  border-bottom:1px solid var(--border)}
#sim-runs td{padding:7px 12px;border-bottom:1px solid var(--border);
  color:var(--text2);vertical-align:middle}
#sim-runs td.mono{font:11px var(--mono);white-space:nowrap}
.sim-st{font:600 10.5px var(--mono)}
.sim-st.running{color:var(--accent)}
.sim-st.completed{color:var(--green)}
.sim-st.failed{color:var(--red)}

#sim-newfeat{background:rgba(251,191,36,.06);border:1px solid #5c4a1e;
  border-radius:10px;padding:12px 16px;display:flex;align-items:center;
  gap:12px;flex-wrap:wrap;font:12.5px var(--sans);color:var(--text2)}
#sim-newfeat b{color:var(--amber);font-weight:600}
#simt-form{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:14px;display:none;flex-direction:column;gap:9px}
#simt-form.open{display:flex}
#simt-form label{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted)}
#simt-form input,#simt-form select{background:var(--panel2);
  border:1px solid var(--border);color:var(--text);border-radius:6px;
  padding:7px 10px;font:12.5px var(--sans)}
#simt-form .row{display:flex;gap:8px;align-items:center}
#sim-tests{display:flex;flex-direction:column;gap:10px}
.simt-card{background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:12px 16px}
.simt-top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.simt-top .n{font:600 14px var(--sans);color:var(--text)}
.simt-top .ts{font:10.5px var(--mono);color:var(--muted)}
.simt-top .cost{margin-left:auto;font:600 12px var(--mono);color:var(--text)}
.simt-path{display:flex;align-items:flex-start;margin-top:12px;
  overflow-x:auto}
.simt-step{flex:1;min-width:110px;display:flex;flex-direction:column;
  align-items:center;text-align:center;position:relative}
.simt-step::before{content:'';position:absolute;top:13px;
  left:calc(-50% + 14px);width:calc(100% - 28px);height:1.5px;
  background:var(--border)}
.simt-step:first-child::before{display:none}
.simt-step .cn{width:26px;height:26px;border-radius:50%;
  border:1.5px solid var(--border);background:var(--panel2);display:grid;
  place-items:center;font:600 10.5px var(--mono);color:var(--muted)}
.simt-step .lbl{margin-top:6px;font-size:11.5px;color:var(--text2)}
.simt-step .st{font:600 9.5px var(--mono);margin-top:2px;color:var(--muted)}
.simt-step.done .cn{border-color:#234534;color:var(--green)}
.simt-step.done::before{background:#234534}
.simt-step.done .st{color:var(--green)}
.simt-step.cur .cn{border-color:var(--accent);color:var(--accent)}
.simt-step.cur .st{color:var(--accent)}
.simt-step.fail .cn{border-color:#552b2b;color:var(--red)}
.simt-step.fail .st{color:var(--red)}
.simt-step .stc{font:600 10px var(--mono);color:var(--text);margin-top:3px}
.simt-det{margin-top:8px;width:100%;max-width:170px;display:flex;
  flex-direction:column;gap:3px;border:1px solid var(--border);
  border-radius:8px;background:var(--panel2);padding:7px 9px}
.simt-det .sdk{display:flex;justify-content:space-between;gap:8px;
  font:9.5px var(--mono);color:var(--muted)}
.simt-det .sdk b{color:var(--text2);font-weight:500;white-space:nowrap}
.simt-actions{display:flex;gap:6px;margin-top:10px}
.sim-meta{display:flex;gap:5px;flex-wrap:wrap}
.sim-meta span{font:10px var(--mono);color:var(--text2);
  border:1px solid var(--border);border-radius:99px;padding:1px 8px}

.fbars{display:grid;grid-template-columns:repeat(auto-fit,
  minmax(210px,1fr));gap:12px 22px;margin-top:12px}
.fbar em{font:600 8.5px var(--sans);font-style:normal;
  text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.fbar .fo{text-transform:none;letter-spacing:0;font-weight:400}
.fbar .fb{display:flex;height:7px;border-radius:99px;overflow:hidden;
  margin:5px 0 6px;background:var(--panel2)}
.fbar .fb span{min-width:3px}
.fbar .fls{font:10px var(--mono);color:var(--text2);line-height:1.6}
.simt-top{cursor:pointer}
.simt-top .chev{color:var(--muted);font-size:12px}
.simt-table{margin-top:12px;border:1px solid var(--border);border-radius:6px;
  overflow-x:auto}
.simt-table table{width:100%;border-collapse:collapse;font-size:12px}
.simt-table th{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted);text-align:left;padding:8px 10px;
  border-bottom:1px solid var(--border)}
.simt-table td{padding:7px 10px;border-bottom:1px solid var(--border);
  color:var(--text2);vertical-align:top}
.simt-table td.mono{font:11px var(--mono);white-space:nowrap}

/* ---------- clients ---------- */
#customers{flex-direction:column;margin:14px 20px 20px;min-height:0;
  background:var(--panel);border:1px solid var(--border);border-radius:8px}
#customers-body{flex:1;overflow-y:auto;min-height:0}
#customers-body table{width:100%;border-collapse:collapse;font-size:12.5px}
#customers-body th{position:sticky;top:0;background:var(--panel);z-index:1;
  font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);text-align:left;padding:9px 14px;
  border-bottom:1px solid var(--border)}
#customers-body td{padding:8px 14px;border-bottom:1px solid var(--border);
  color:var(--text2);vertical-align:top}
#customers-body td.cid{font:600 13px var(--mono);color:var(--accent);
  white-space:nowrap}
#customers-body td.cname{color:var(--text)}
#customers-body td.cmono{font:11px var(--mono);white-space:nowrap}
.clients-empty{padding:30px;text-align:center;color:var(--muted)}
#pipelines{flex-direction:column;margin:14px 20px 20px;gap:12px;min-height:0}

/* ---------- live log: a terminal ---------- */
body.view-activity #main{overflow:hidden}
#activity{flex:1;flex-direction:column;margin:14px 20px 20px;min-height:0;
  background:#0a0a0a;border:1px solid var(--border);border-radius:8px;
  overflow:hidden}
#activity-head{display:flex;align-items:center;gap:7px;padding:8px 14px;
  background:var(--panel);border-bottom:1px solid var(--border)}
#activity-head .dot{width:11px;height:11px;border-radius:50%;flex:none}
#activity-head .t{font:600 11px var(--mono);color:var(--text2);margin-left:8px}
#activity-head .m{font:10px var(--mono);color:var(--muted);margin-left:auto}
.activity-filter{background:var(--panel2);border:1px solid var(--border);
  color:var(--text2);border-radius:5px;padding:2px 6px;
  font:10.5px var(--mono);max-width:140px;margin-left:8px}
.activity-filter:focus{outline:none;border-color:var(--border-hi)}
#activity-body{flex:1;overflow-y:auto;min-height:0;padding:12px 14px;
  font:11.5px/1.75 var(--mono);color:#c8c8c8;overflow-wrap:break-word}
#activity-body .ln{white-space:pre-wrap}
#activity-body .ts{color:#5c6370}
#activity-body .pr{color:#61afef}
#activity-body .ev{color:#98c379}
#activity-body .ev.err{color:#e06c75;font-weight:600}
#activity-body .ev.warn{color:#e5c07b}
#activity-body .ac{color:#c678dd}
#activity-body .dt{color:#7f848e}
#activity-body .prj{color:#d19a66;font-weight:600}
#activity-body .ln.dim span{color:#4b5263}
#activity-body .who{font-weight:600}
#activity-body .who.client{color:#e5c07b}
#activity-body .who.bot{color:#56b6c2}
#activity-body .mt{color:#dcdcdc}
#activity-body .activity-table{width:100%;border-collapse:collapse;font-size:12px;
  font-family:var(--sans)}
#activity-body .activity-table th{position:sticky;top:-12px;background:#111;z-index:1;
  font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);text-align:left;padding:8px 10px;
  border-bottom:1px solid var(--border)}
#activity-body .activity-table td{padding:6px 10px;border-bottom:1px solid #1c1c1c;
  color:var(--text2);vertical-align:top}
#activity-body .activity-table td.cmono{font:11px var(--mono);white-space:nowrap}
#activity-body .cursor{display:inline-block;width:7px;height:13px;
  background:#98c379;vertical-align:-2px;animation:blink 1.1s step-end infinite}
@keyframes blink{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){#activity-body .cursor{animation:none}}

/* ---------- chatbot flow cards ---------- */
#pipelines-list{display:flex;flex-direction:column;gap:12px}
.flow-card{background:var(--panel);border:1px solid var(--border);border-radius:8px}
.fc-head{display:flex;align-items:center;gap:10px;padding:11px 16px;cursor:pointer;
  flex-wrap:wrap}
.fc-head:hover{background:var(--panel2)}
.fc-name{font-size:16px;font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:340px}
.fc-id{font:10.5px var(--mono);color:var(--muted)}
.fc-test{font:600 9px var(--mono);text-transform:uppercase;letter-spacing:.06em;
  color:var(--amber);border:1px solid #5c4a1e;border-radius:99px;padding:2px 7px}
.fc-state{font:12px var(--sans);color:var(--text2)}
.fc-left{display:flex;flex-direction:column;min-width:0;margin-right:4px;gap:4px}
.fc-idbig{font:600 17px var(--sans);color:var(--text);white-space:nowrap;
  display:flex;align-items:center;gap:8px;letter-spacing:-.01em}
.fc-ts{font:10.5px var(--mono);color:var(--muted);white-space:nowrap}
.fc-client{font:600 11px var(--mono);color:var(--amber);white-space:nowrap}
.fc-flowstate{font:600 11.5px var(--sans);color:var(--accent);margin-top:2px;
  display:flex;align-items:center;gap:7px}
.fc-flowstate .spin{width:11px;height:11px}
.fc-flowstate .fs-done{color:var(--green)}
.fc-flowstate:has(.fs-done){color:var(--green)}
.fc-cost{margin-left:auto;display:flex;flex-direction:column;
  align-items:flex-end;white-space:nowrap}
.fc-cost em{font:600 8.5px var(--sans);font-style:normal;text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted)}
.fc-cost b{font:600 13px var(--mono);color:var(--text)}
.fc-path{display:flex;align-items:flex-start;padding:6px 16px 16px;gap:0;
  overflow-x:auto}
.fc-step{flex:1;min-width:150px;display:flex;flex-direction:column;
  align-items:center;text-align:center;position:relative}
/* the Interview step carries the stacked metric cards: just enough room
   for the single narrow column, so the path keeps its width for the rest */
.fc-step:first-child{min-width:230px}
.fc-step .cn{width:34px;height:34px;border-radius:50%;border:1.5px solid var(--border);
  background:var(--panel2);display:grid;place-items:center;
  font:600 12px var(--mono);color:var(--muted);z-index:1;position:relative}
.fc-step .cn .ico{font-size:15px;line-height:1;filter:grayscale(35%)}
.fc-step.planned .cn .ico,.fc-step.unmet .cn .ico{filter:grayscale(90%);opacity:.8}
.fc-step .cn .numb{position:absolute;top:-5px;right:-7px;width:15px;height:15px;
  border-radius:50%;background:var(--panel);border:1px solid var(--border-hi);
  font:600 9px var(--mono);color:var(--text2);display:grid;place-items:center}
.fc-step.completed .cn .numb{color:var(--green);border-color:#234534}
.fc-step.current .cn .numb{color:var(--accent);border-color:var(--accent)}
.fc-step .mline{font:10px var(--mono);color:var(--text2);margin-top:4px;
  line-height:1.5}
.fc-step .mline b{color:var(--text);font-weight:500}
.fc-step .mrow{display:flex;gap:5px;margin-top:4px;flex-wrap:nowrap;
  justify-content:center;align-items:stretch}
.fc-step .mcol{display:flex;flex-direction:column;align-items:center;gap:3px;
  padding:0 6px}
.fc-step .mcol em{font:600 8px var(--sans);font-style:normal;
  text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.fc-step .mcol>b{font:500 11px var(--mono);color:var(--text)}
.fc-step .mcol .msub{font:9.5px var(--mono);color:var(--muted)}
.fc-step .mcard{display:flex;flex-direction:column;align-items:center;gap:6px;
  border:1px solid var(--border);border-radius:10px;background:var(--panel2);
  padding:10px 12px;width:158px;flex:none}
/* ONE compact metric box, paired left/right: costs on top (Human | Bot),
   tokens (Input | Output), message pills (Received | Sent), a slim timing
   row, then View Input | View Output. Fixed width, fixed font sizes. */
.fc-step .mstack{flex-direction:column;align-items:stretch;width:216px}
.fc-step .mstack .mcard{width:100%;gap:9px}
.fc-step .mpair{display:grid;grid-template-columns:1fr 1fr;width:100%;
  gap:4px;align-items:start;justify-items:center}
.fc-step .mpair.mtime{grid-template-columns:1fr 1.2fr 1fr}
.fc-step .mpair .mcol{padding:0;min-width:0}
.fc-step .mpill{border:1px solid var(--border-hi);border-radius:99px;
  padding:1px 11px}
.fc-step .mtotal{font:600 12px var(--mono);color:var(--text);
  padding:2px 14px}
.fc-step .mmodel{font:9.5px var(--mono);color:var(--muted);margin-top:2px;
  border:1px solid var(--border);border-radius:99px;padding:1px 8px}
/* View pins to the card bottom; cards stretch to equal height */
.fc-step .mcard .mjson{margin-top:auto}
.fc-step .mjson{font:9.5px var(--mono);color:var(--text2);
  border:1px solid var(--border);border-radius:99px;padding:2px 7px;
  white-space:nowrap}
.fc-step .mjson b{color:var(--text);font-weight:500}
.fc-step .mjson a{color:var(--accent);text-decoration:none}
.fc-step .mjson a:hover{text-decoration:underline}
/* label / bot / status / metrics stack under each other */
.fc-step>div{display:flex;flex-direction:column;align-items:center;min-width:0}
.fc-step .lbl{margin-top:7px;font-size:13px;color:var(--text2);line-height:1.3}
.fc-step .who{font-size:11px;color:var(--muted);margin-top:2px}
.fc-step .st{font:600 10px var(--mono);margin-top:3px;color:var(--muted);
  max-width:150px;line-height:1.45}
.fc-step::before{content:'';position:absolute;top:17px;left:calc(-50% + 17px);
  width:calc(100% - 34px);height:1.5px;background:var(--border)}
.fc-step:first-child::before{display:none}
.fc-step.completed .cn{border-color:#234534;color:var(--green)}
.fc-step.completed::before{background:#234534}
.fc-step.current .cn{border-color:var(--accent);color:var(--accent)}
.fc-step.current .lbl{color:var(--text);font-weight:600}
.fc-step.current .st{color:var(--accent)}
.fc-step.completed .st{color:var(--green)}
.fc-step .st .spin{width:9px;height:9px;margin-right:5px;vertical-align:-1px}
.fc-step.current.busy .cn::after{content:'';position:absolute;inset:-5px;
  border-radius:50%;pointer-events:none;
  background:conic-gradient(from 0deg, transparent 0 12%,
    rgba(110,168,254,.12) 35%, rgba(110,168,254,.55) 75%, var(--accent) 100%);
  -webkit-mask:radial-gradient(closest-side,transparent calc(100% - 5px),
    #000 calc(100% - 4px));
  mask:radial-gradient(closest-side,transparent calc(100% - 5px),
    #000 calc(100% - 4px));
  animation:spin 1.6s linear infinite}
@media (prefers-reduced-motion:reduce){.fc-step.current.busy .cn::after{animation:none}}
.fc-step.blocked .st{color:var(--amber)}
.fc-step.blocked .cn{border-color:#5c4a1e;color:var(--amber)}
.fc-step.planned .cn{border-style:dashed;opacity:.65}
.fc-step.planned .lbl,.fc-step.planned .who,.fc-step.planned .st{opacity:.65}
.fc-step.unmet .cn{border-style:dashed;opacity:.65}
.fc-step.unmet .lbl{opacity:.65}
.fc-detail{display:none;border-top:1px solid var(--border)}
.flow-card.open .fc-detail{display:block}
.fd-body{background:var(--panel2);margin:0 14px 14px;border:1px solid var(--border);
  border-radius:0 6px 6px 6px;padding:12px 14px;max-height:320px;overflow-y:auto;
  font-size:12.5px;line-height:1.6}
.fd-body table{width:100%;border-collapse:collapse;font-size:12px}
.fd-body td,.fd-body th{padding:4px 8px;border-bottom:1px solid var(--border);
  text-align:left;vertical-align:top}
.fd-body th{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted)}
.fd-msg{max-width:78%;border:1px solid var(--border);border-radius:6px;
  padding:6px 10px;margin:5px 0;white-space:pre-wrap;overflow-wrap:break-word}
.fd-msg.human{margin-left:auto;background:#161a20;border-color:#2b3a52}
.fd-msg.ai{background:var(--panel)}
.fd-msg .who{font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);margin-bottom:2px}
/* shared empty state: centered on screen, one style for every empty tab */
.empty-state{min-height:62vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:20px}
.empty-state b{font:500 15px var(--sans);color:var(--text);margin-bottom:7px}
.empty-state span{font:13px var(--sans);color:var(--muted);max-width:420px;
  line-height:1.6}
@media (max-width:900px){
  .fc-path{flex-direction:column;align-items:stretch;gap:10px;overflow:visible}
  .fc-step{flex-direction:row;text-align:left;gap:12px;min-width:0;
    align-items:center}
  .fc-step .lbl{margin-top:0}
  .fc-step .st{max-width:none}
  .fc-step::before{display:none}
  .fc-step>div{align-items:flex-start}
}
/* Home is the full remaining workspace: no narrow card, no page scroll */
#overview{flex:1;flex-direction:column;min-height:0;overflow:hidden}
body.view-overview #main{overflow:hidden}

/* ---------- Home: header-free, the map IS the page ---------- */
body.view-overview #run-header,body.view-pipelines #run-header,
body.view-activity #run-header{display:none}
body.view-customers #main{overflow:hidden}

/* ---------- orchestrator map: fills the workspace ---------- */
#graph-wrap{flex:1;min-height:0;position:relative;display:flex;
  align-items:center;justify-content:center;background:var(--bg)}
#graph{display:block;width:100%;height:100%}
#overview-stale{position:absolute;top:10px;right:16px;font:10px var(--mono);
  color:var(--muted);pointer-events:none}
#overview-stale.bad{color:var(--red)}
.gnode{cursor:pointer}
.gnode:focus{outline:none}
.gnode:focus>circle.body{stroke:var(--accent)}
.gnode.planned{cursor:default;opacity:.6}
.gnode text{font-family:var(--sans)}
.gedge{stroke:var(--border);stroke-width:1.2}
.gedge.planned{stroke-dasharray:2 5;opacity:.5}
.gedge.active{stroke:var(--accent);stroke-dasharray:6 6;animation:dashmove 1s linear infinite}
@keyframes dashmove{to{stroke-dashoffset:-12}}
/* StatusRing — "Comet tail": a bright head dragging a fading gradient tail
   around the node border. Implemented as HTML overlay rings (rotating
   conic-gradient masked to a thin edge band) positioned 1:1 over the SVG
   nodes — this animates reliably everywhere, including the big center
   circle where CSS-rotated SVG strokes could stay frozen. */
.gring{position:absolute;border-radius:50%;pointer-events:none;
  background:conic-gradient(from 0deg, transparent 0 12%,
    rgba(110,168,254,.12) 35%, rgba(110,168,254,.55) 75%, var(--accent) 100%);
  -webkit-mask:radial-gradient(closest-side,transparent calc(100% - 6px),
    #000 calc(100% - 5px));
  mask:radial-gradient(closest-side,transparent calc(100% - 6px),
    #000 calc(100% - 5px));
  animation:spin linear infinite}
.gring.idle{animation-duration:7s;opacity:.75}
.gring.running{animation-duration:1.6s;opacity:1;
  filter:drop-shadow(0 0 5px rgba(110,168,254,.6))}
.gring.center-idle{animation-duration:9s;opacity:.8}
.gring.center-active{animation-duration:2.2s;opacity:1;
  filter:drop-shadow(0 0 6px rgba(110,168,254,.6))}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){
  .gedge.active,.gring{animation:none}
}
/* ---------- hierarchical map nodes (HTML circles, animated) ---------- */
.hnode{position:absolute;border-radius:50%;background:var(--panel2);
  border:1.2px solid var(--border-hi);display:flex;align-items:center;
  justify-content:center;text-align:center;cursor:pointer;z-index:2;
  transition:left .5s ease,top .5s ease,width .5s ease,height .5s ease,
    opacity .45s ease}
.hnode:focus{outline:none;border-color:var(--accent)}
.hnode.planned{border-style:dashed;cursor:default}
.hnode .ttl{font-weight:600;color:var(--text);line-height:1.18;
  transition:font-size .5s ease;pointer-events:none}
.hnode .npill{position:absolute;bottom:-12px;left:50%;
  transform:translateX(-50%);white-space:nowrap;pointer-events:none;
  font:10.5px var(--sans);color:var(--text);background:rgba(16,18,22,.55);
  backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);
  border:1px solid rgba(110,168,254,.35);border-radius:99px;padding:2.5px 11px}
.hnode .npill .dot{font-size:9px}
.hnode .nring{position:absolute;inset:-5px;border-radius:50%;
  pointer-events:none;display:none;
  background:conic-gradient(from 0deg, transparent 0 12%,
    rgba(110,168,254,.12) 35%, rgba(110,168,254,.55) 75%, var(--accent) 100%);
  -webkit-mask:radial-gradient(closest-side,transparent calc(100% - 6px),
    #000 calc(100% - 5px));
  mask:radial-gradient(closest-side,transparent calc(100% - 6px),
    #000 calc(100% - 5px));
  animation:spin linear infinite}
.hnode .nring.idle{display:block;animation-duration:7s;opacity:.75}
.hnode .nring.running{display:block;animation-duration:1.6s;opacity:1;
  filter:drop-shadow(0 0 5px rgba(110,168,254,.6))}
.hnode .nring.center-idle{display:block;animation-duration:9s;opacity:.8}
.hnode .nring.center-active{display:block;animation-duration:2.2s;opacity:1;
  filter:drop-shadow(0 0 6px rgba(110,168,254,.6))}
.hbtn{position:absolute;transform:translateX(-50%);z-index:2;cursor:pointer;
  background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);
  border-radius:13px;padding:5px 14px;font:600 11.5px var(--sans);
  transition:left .5s ease,top .5s ease,opacity .45s ease}
.hbtn:hover,.hbtn:focus{border-color:var(--accent)}
.gedge.fadein{animation:edgein .35s ease}
@keyframes edgein{from{opacity:0}to{opacity:1}}
@media (prefers-reduced-motion:reduce){
  .hnode,.hbtn,.hnode .ttl{transition:none}
  .hnode .nring{animation:none}
}

/* frosted-glass status pill on each node's bottom border (chosen style 5) */
.gpill{position:absolute;transform:translate(-50%,-50%);z-index:3;
  pointer-events:none;white-space:nowrap;
  font:10.5px var(--sans);color:var(--text);
  background:rgba(16,18,22,.55);
  backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);
  border:1px solid rgba(110,168,254,.35);border-radius:99px;
  padding:2.5px 11px}
.gpill .dot{font-size:9px}

/* the New Session action pinned under the RequirementsBot node */
.gbtn{cursor:pointer}
.gbtn rect{fill:#1d2a3f;stroke:#2b3a52;rx:13}
.gbtn:hover rect,.gbtn:focus rect{stroke:var(--accent)}
.gbtn:focus{outline:none}
.gbtn text{fill:var(--text);font:600 11.5px var(--sans)}

/* ---------- floating supervisor (owner-only) ---------- */
#sup-fab{position:fixed;right:22px;bottom:22px;z-index:60;width:52px;height:52px;
  border-radius:50%;background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);
  font:600 13px var(--sans);cursor:pointer;display:grid;place-items:center;
  box-shadow:0 6px 24px rgba(0,0,0,.5)}
#sup-fab:hover{border-color:var(--accent)}
#sup-fab .fab-dot{position:absolute;top:3px;right:3px;width:9px;height:9px;
  border-radius:50%;border:2px solid var(--bg);background:var(--muted)}
#sup-fab .fab-dot.on{background:var(--green)}
#sup-fab-label{position:fixed;right:48px;bottom:2px;z-index:60;
  transform:translateX(50%);white-space:nowrap;pointer-events:none;
  font:9.5px var(--sans);color:var(--text);
  background:rgba(16,18,22,.55);
  backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px);
  border:1px solid rgba(110,168,254,.35);border-radius:99px;
  padding:2px 9px}
#sup-fab-label .dot{font-size:8px}
.overlay{position:fixed;right:22px;bottom:84px;z-index:60;width:360px;
  max-width:calc(100vw - 44px);height:480px;max-height:calc(100vh - 130px);
  background:var(--panel);border:1px solid var(--border);border-radius:8px;
  display:none;flex-direction:column;box-shadow:0 14px 44px rgba(0,0,0,.55)}
.overlay.open{display:flex}
.op-head{display:flex;align-items:center;gap:8px;padding:9px 14px;
  border-bottom:1px solid var(--border);font:600 11px var(--sans);
  text-transform:uppercase;letter-spacing:.07em;color:var(--text2)}
.overlay .op-head{border-radius:8px 8px 0 0}
.ov-close{margin-left:6px;background:none;border:none;color:var(--muted);
  cursor:pointer;font-size:14px}
.ov-close:hover{color:var(--text)}
#mgmt-thread{flex:1;overflow-y:auto;padding:12px 14px;display:flex;
  flex-direction:column;gap:9px;min-height:120px}
#mgmt-bar{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border);
  align-items:flex-end}
#mgmt-input{flex:1;background:var(--panel2);border:1px solid var(--border);
  color:var(--text);border-radius:6px;padding:7px 10px;font:12.5px/1.5 var(--sans);
  resize:none;min-height:34px;max-height:110px}
#mgmt-input:focus{outline:none;border-color:var(--border-hi)}
.mmsg{max-width:85%;border:1px solid var(--border);border-radius:6px;
  padding:6px 10px;font-size:12.5px;line-height:1.5;white-space:pre-wrap;
  overflow-wrap:break-word}
.mmsg .who{font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);margin-bottom:2px}
.mmsg.operator{align-self:flex-end;background:#161a20;border-color:#2b3a52}
.mmsg.supervisor{align-self:flex-start;background:var(--panel2)}
.mmsg.system{align-self:center;max-width:none;border-style:dashed;
  color:var(--muted);font:11px var(--mono)}

/* ---------- centered content viewer ---------- */
#viewer{position:fixed;inset:0;z-index:70;display:none;align-items:center;
  justify-content:center;background:rgba(0,0,0,.62)}
#viewer.open{display:flex}
#viewer .vbox{background:var(--panel);border:1px solid var(--border);
  border-radius:10px;width:min(700px,92vw);max-height:80vh;display:flex;
  flex-direction:column;box-shadow:0 18px 60px rgba(0,0,0,.6)}
#viewer-pre{flex:1;overflow:auto;margin:0;padding:14px 16px;
  font:11.5px/1.6 var(--mono);color:var(--text2);white-space:pre-wrap;
  overflow-wrap:break-word}
#viewer-foot{display:flex;justify-content:flex-end;gap:8px;
  padding:10px 14px;border-top:1px solid var(--border)}
#viewer-foot .act{text-decoration:none}

/* ---------- attention overlay ---------- */
#att-list{flex:1;overflow-y:auto;padding:10px 14px;display:flex;
  flex-direction:column;gap:8px}
.att-item{display:flex;align-items:flex-start;gap:10px;border:1px solid var(--border);
  border-radius:6px;padding:8px 12px;font-size:12.5px;background:var(--panel2)}
.att-item .blk{flex:none;font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.05em;padding:2px 7px;border-radius:99px;margin-top:1px}
.att-item .blk.b1{color:var(--red);border:1px solid #552b2b}
.att-item .blk.b0{color:var(--amber);border:1px solid #5c4a1e}
.att-item .txt{flex:1;line-height:1.5}
.att-item .meta{font:10px var(--mono);color:var(--muted)}
.att-empty{color:var(--muted);font-size:12px;padding:14px}

/* ---------- sessions (owner testing chat) & review ---------- */
#chat{flex:1;flex-direction:column;margin:14px 20px 20px;min-height:0;
  background:var(--panel);border:1px solid var(--border);border-radius:6px}
#chat-head{display:flex;align-items:center;gap:10px;padding:9px 14px;
  border-bottom:1px solid var(--border);flex-wrap:wrap}
#chat-head .t{font:600 11px var(--sans);text-transform:uppercase;
  letter-spacing:.07em;color:var(--text2)}
#chat-head .m{font:10.5px var(--mono);color:var(--muted)}
.proj-select{background:var(--panel2);border:1px solid var(--border);color:var(--text);
  border-radius:5px;padding:4px 8px;font:12px var(--sans);max-width:260px}
#chat-reset{margin-left:auto}
#chat-thread{flex:1;overflow-y:auto;padding:16px 18px;display:flex;
  flex-direction:column;gap:12px;min-height:0}
.msg{max-width:72%;border:1px solid var(--border);border-radius:6px;
  padding:8px 12px;font-size:13px;line-height:1.55;white-space:pre-wrap;
  overflow-wrap:break-word}
.msg .who{font:600 9.5px var(--mono);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);margin-bottom:3px}
.msg.ai{align-self:flex-start;background:var(--panel2)}
.msg.human{align-self:flex-end;background:#161a20;border-color:#2b3a52}
.msg.err{align-self:stretch;max-width:none;border-color:#553030;color:var(--red);
  font-family:var(--mono);font-size:11.5px}
.msg.sys{align-self:center;max-width:none;border:none;background:none;
  color:var(--green);font:11px var(--mono)}
#chat-typing{align-self:flex-start;color:var(--muted);font:11.5px var(--mono);
  padding:2px 4px}
#chat-typing .spin{margin-right:6px;vertical-align:-1px}
#chat-bar{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border);
  align-items:flex-end}
#chat-input{flex:1;background:var(--panel2);border:1px solid var(--border);
  color:var(--text);border-radius:6px;padding:8px 12px;font:13px/1.5 var(--sans);
  resize:none;min-height:38px;max-height:140px}
#chat-input:focus{outline:none;border-color:var(--border-hi)}
#chat-send{background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);
  border-radius:6px;padding:8px 16px;font:600 12px var(--sans);cursor:pointer}
#chat-send:hover{border-color:var(--accent)}
#chat-send:disabled{opacity:.5;cursor:default}
#chat-attach{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:6px;padding:8px 11px;font-size:13px;cursor:pointer}
#chat-attach:hover{border-color:var(--border-hi);color:var(--text)}
#chat-thread.drop{outline:1px dashed var(--accent);outline-offset:-6px}

@media (max-width:760px){
  #sidebar{display:none}
  body.view-overview #main{overflow-y:auto}
  #overview{overflow:visible}
  #graph-wrap{min-height:420px}
}
</style></head><body class="view-overview">
<div id="shell">
  <aside id="sidebar">
    <div id="sb-head"><div id="sb-logo" title="BotBot Orchestrator" aria-label="BotBot Orchestrator">B</div><span id="sb-title">BotBot Orchestrator</span>
      <button id="sb-toggle" title="Collapse">⟨⟩</button></div>
    <nav id="sb-nav">
      <div class="nav-item active" id="nav-overview" data-view="overview"><span class="nav-ico">◎</span><span class="nav-label">Overview</span></div>
      <div class="nav-item" id="nav-activity" data-view="activity"><span class="nav-ico">&gt;_</span><span class="nav-label">Activity</span></div>
      <div class="nav-item" id="nav-pipelines" data-view="pipelines"><span class="nav-ico">⇶</span><span class="nav-label">Pipelines</span></div>
      <div class="nav-item" id="nav-experiments" data-view="experiments"><span class="nav-ico">⚗</span><span class="nav-label">Experiment suites</span></div>
      <div class="nav-item" id="nav-testsuites" data-view="testsuites"><span class="nav-ico">⌖</span><span class="nav-label">Test suites</span></div>
      <div class="nav-item" id="nav-customers" data-view="customers"><span class="nav-ico">◉</span><span class="nav-label">Customers</span></div>
      <div class="nav-item" id="nav-ratelimits" data-view="ratelimits"><span class="nav-ico">⇅</span><span class="nav-label">Rate limits</span></div>
      <div class="nav-item" id="nav-billing" data-view="billing"><span class="nav-ico">$</span><span class="nav-label">Billing</span></div>
    </nav>
    <div id="sb-foot">
      <div><span class="dot" id="dot-store"></span><span id="txt-store">Database: checking…</span></div>
      <div><span class="dot" id="dot-api"></span><span id="txt-api">Claude API: checking…</span></div>
      <div><span class="dot" id="dot-queue"></span><span id="txt-queue">API queue: checking…</span></div>
      <div id="rl-bars"></div>
    </div>
  </aside>

  <div id="main">
    <div id="run-header">
      <div><div id="run-title">Operations Console</div></div>
      <div id="run-actions"></div>
    </div>

    <div id="overview">
      <div id="graph-wrap">
        <svg id="graph" role="img" aria-label="Orchestrator map"></svg>
        <span id="overview-stale"></span>
      </div>
    </div>

    <div id="pipelines">
      <div id="pipelines-list"></div>
    </div>

    <div id="customers">
      <div id="customers-body">Loading…</div>
    </div>

    <div id="ratelimits">
      <div id="ratelimits-body">Loading…</div>
    </div>

    <div id="billing">
      <div id="billing-body">Loading…</div>
    </div>

    <div id="sim">
      <div class="sim-head"><span class="t" id="simt-mode-label"></span>
        <span class="m" id="simt-mode-desc"></span></div>
      <div id="sim-newfeat" style="display:none"></div>
      <div id="simt-form">
        <span id="simt-f-kind"><label>Test type</label>
        <select id="simt-kind">
          <option value="persona_sweep">Persona Sweep &amp; Analysis — N fake
            businesses interview the bot; AI analyzes all inputs and outputs,
            reports problems and drafts the fixes</option>
          <option value="stress">Single-dimension stress test — every persona
            pinned to one level of one dimension (isolates a variable)</option>
          <option value="regression_pin">Regression pin — re-run the EXACT
            personas of a previous test against the current bot and diff the
            scores</option>
          </select></span>
        <span id="simt-f-count"><label>How many different business
          personas</label>
        <input id="simt-count" type="number" min="2" max="8" value="4"
          style="width:90px"></span>
        <span id="simt-f-stress" style="display:none">
          <label>Pinned dimension and level</label>
          <span class="row">
          <select id="simt-dim">
            <option value="knowledge">Owner knowledge</option>
            <option value="patience" selected>Patience</option>
            <option value="consistency">Consistency</option>
            <option value="clarity">Clarity</option>
            <option value="trust">Trust</option>
            <option value="language_mix">Language mix</option>
            <option value="typing">Typing quality</option>
            <option value="focus">Focus</option></select>
          <select id="simt-level">
            <option value="0.15" selected>15% — hardest case</option>
            <option value="0.4">40%</option>
            <option value="0.7">70%</option>
            <option value="1.0">100% — best case</option></select></span></span>
        <span id="simt-f-base" style="display:none">
          <label>Base test number to re-run</label>
          <input id="simt-base" type="number" min="1" style="width:90px"></span>
        <span id="simt-f-features" style="display:none">
          <label>Features to check — tick any number; each ticked feature
            becomes its own parallel test with a YES/NO verdict</label>
          <div id="simt-featchips">Loading feature catalog…</div></span>
        <div class="row"><button class="act" id="simt-start">Start test</button>
          <span class="m" id="simt-note">estimated cost: ~$0.20-0.40 per
            persona, plus the analysis (Haiku ~$0.10; regression pins skip
            AI analysis entirely)</span></div>
      </div>
      <div id="sim-tests" style="flex:1;min-height:0;overflow-y:auto"></div>
    </div>

    <div id="activity">
      <div id="activity-head">
        <input class="activity-filter" id="lf-proj" type="text" inputmode="numeric"
          placeholder="Project #" aria-label="Filter by project number"
          spellcheck="false">
        <input class="activity-filter" id="lf-client" type="text" inputmode="numeric"
          placeholder="Client #" aria-label="Filter by client number"
          spellcheck="false">
        <select class="activity-filter" id="lf-bot" aria-label="Filter by bot">
          <option value="">All bots</option>
          <option value="RequirementBot">RequirementBot</option></select>
        <select class="activity-filter" id="lf-kind" aria-label="Filter by activity type">
          <option value="">All activity</option>
          <optgroup label="Conversations">
            <option value="cat:conversations">All conversations</option>
            <option value="msg:owner">Client → Bot</option>
            <option value="msg:bot">Bot → Client</option>
          </optgroup>
          <optgroup label="Clicks">
            <option value="cat:clicks">All clicks</option>
            <option value="click:operator">Operator clicks</option>
            <option value="click:client">Client clicks</option>
          </optgroup>
          <optgroup label="Milestones">
            <option value="cat:milestones">All milestones</option>
            <option value="type:project.created">project.created</option>
            <option value="type:materials.uploaded">materials.uploaded</option>
            <option value="type:interview.closed">interview.closed</option>
            <option value="type:readiness.evaluated">readiness.evaluated</option>
            <option value="type:review.resolved">review.resolved</option>
            <option value="type:approval.recorded">approval.recorded</option>
            <option value="type:approval.invalidated">approval.invalidated</option>
            <option value="type:export.created">export.created</option>
          </optgroup>
          <optgroup label="Needs attention">
            <option value="cat:attention">All needs-attention</option>
            <option value="type:review.requested">review.requested</option>
            <option value="type:interview.reset">interview.reset</option>
          </optgroup>
          <optgroup label="Failures">
            <option value="cat:failures">All failures</option>
            <option value="type:run.failed">run.failed</option>
            <option value="type:supervisor.failed">supervisor.failed</option>
          </optgroup>
          <optgroup label="Housekeeping">
            <option value="type:revision.committed">revision.committed</option>
          </optgroup>
        </select>
        <span class="m" id="activity-meta">connecting…</span>
        <button class="act" id="activity-mode" style="margin-left:10px"
          title="Switch between raw terminal and a structured table">Structured log</button></div>
      <div id="activity-body">Loading…</div>
    </div>

    <div id="chat">
      <div id="chat-head">
        <button class="act" id="chat-back" title="Back to Pipelines">←</button>
        <span class="t" style="color:var(--amber)">Operator Test Mode</span>
        <select class="proj-select" id="proj-select-chat" aria-label="Select test session"></select>
        <button class="act" id="proj-new">+ New test session</button>
        <span class="m">same engine as the client link · test sessions only</span>
        <button class="act" id="chat-reset" title="Start a new interview">↺ New interview</button></div>
      <div id="chat-thread"></div>
      <div id="chat-bar">
        <input type="file" id="chat-file" multiple hidden
               accept=".txt,.md,.csv,.png,.jpg,.jpeg,.webp,.gif">
        <button id="chat-attach" title="Attach chat exports / screenshots">📎</button>
        <textarea id="chat-input" rows="1" placeholder="Type your answer… (Enter to send, Shift+Enter for newline)" spellcheck="false"></textarea>
        <button id="chat-send">Send</button>
      </div>
    </div>
  </div>
</div>

<button id="sup-fab" title="AI Supervisor (system scope)" aria-label="Open AI supervisor chat"
  aria-expanded="false">AI<span class="fab-dot" id="fab-dot"></span></button>
<div id="sup-fab-label" aria-hidden="true">supervisor: —</div>
<div id="sup-drawer" class="overlay" role="dialog" aria-label="AI supervisor chat">
  <div class="op-head">AI Supervisor · System
    <span class="badge idle" id="sup-mode"><span class="b-dot"></span><span id="sup-mode-txt">—</span></span>
    <button class="act" id="sup-toggle" style="margin-left:auto">…</button>
    <button class="ov-close" id="sup-close" title="Close" aria-label="Close">✕</button></div>
  <div id="mgmt-thread"></div>
  <div id="mgmt-bar">
    <textarea id="mgmt-input" rows="1" placeholder="Ask about the platform…" spellcheck="false"></textarea>
    <button class="act" id="mgmt-send">Ask</button>
  </div>
</div>
<div id="viewer" role="dialog" aria-modal="true" aria-label="Content viewer">
  <div class="vbox">
    <div class="op-head"><span id="viewer-title">—</span>
      <button class="ov-close" id="viewer-close" title="Close" aria-label="Close"
        style="margin-left:auto">✕</button></div>
    <pre id="viewer-pre"></pre>
    <div id="viewer-foot"><span id="viewer-dl"></span></div>
  </div>
</div>
<div id="att-drawer" class="overlay" role="dialog" aria-label="Needs attention">
  <div class="op-head">Needs Attention
    <button class="ov-close" id="att-close" title="Close" aria-label="Close" style="margin-left:auto">✕</button></div>
  <div id="att-list"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
$('#sb-toggle').onclick = () => {
  $('#sidebar').classList.toggle('collapsed');
  if(document.body.className === 'view-overview') renderGraph();
};
const VIEW_TITLES = {overview:'Overview', pipelines:'Pipelines',
  chat:'Operator Test Chat', activity:'Activity', customers:'Customers',
  experiments:'Experiment Suites', testsuites:'Test Suites',
  ratelimits:'Rate Limits', billing:'Billing'};
function showView(view){
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  document.getElementById('nav-' + (view === 'chat' ? 'pipelines' : view))
    ?.classList.add('active');
  document.body.className = 'view-' + view;
  $('#run-title').textContent = VIEW_TITLES[view] || 'Operations';
  if(view === 'chat' && !chat.loaded) loadChat();
  if(view === 'overview') loadSystem();
  if(view === 'pipelines') loadPipelines();
  if(view === 'activity') loadActivity();
  if(view === 'customers') loadCustomers();
  if(view === 'experiments') openSimTab('training');
  if(view === 'testsuites') openSimTab('feature_check');
  if(view === 'ratelimits') loadRateLimits();
  if(view === 'billing') loadBilling();
}
document.querySelectorAll('.nav-item[data-view]').forEach(item =>
  item.onclick = () => showView(item.dataset.view));
$('#chat-back').onclick = () => showView('pipelines');

/* ================= system-wide Home ================= */
let sys = null;   // last /api/system payload

async function loadSystem(){
  try{ sys = await (await fetch('/api/system')).json(); }
  catch(e){
    $('#overview-stale').textContent = 'stale — server unreachable';
    $('#overview-stale').className = 'bad';
    return;
  }
  $('#overview-stale').textContent = '';
  $('#overview-stale').className = '';
  const s = sys;
  const lc = s.health.last_call;

  // honest footer
  const api = s.health.api || {status: 'checking'};
  const apiCls = api.status === 'connected' ? 'ok'
    : api.status === 'checking' ? ''
    : (api.status === 'rate limited' || api.status === 'Anthropic overloaded')
      ? 'warn' : 'bad';
  $('#dot-api').className = 'dot ' + apiCls;
  $('#txt-api').textContent = 'Claude API: ' + api.status;
  $('#txt-api').title = api.detail || '';
  $('#dot-store').className = 'dot ' + (s.health.store_ok ? 'ok' : 'bad');
  $('#txt-store').textContent = 'Database: ' + (s.health.store_ok ? 'connected' : 'error');
  $('#txt-store').title = s.health.error || '';
  renderRateLimits(api.limits || {});
  const mode = s.health.supervisor_mode;
  $('#sup-mode').className = 'badge ' + (mode==='advisory' ? 'completed' : 'idle');
  $('#sup-mode-txt').textContent = mode==='advisory' ? 'Advisory' : 'Disabled';
  $('#sup-toggle').textContent = mode==='advisory' ? 'Disable' : 'Enable advisory mode';
  $('#fab-dot').className = 'fab-dot' + (mode==='advisory' ? ' on' : '');
  $('#sup-fab-label').innerHTML =
    `<span class="dot" style="color:${mode==='advisory'?'var(--green)':'var(--muted)'}">●</span> `
    + (mode==='advisory' ? 'Advisory' : 'Disabled');
  $('#sup-fab-label').title = 'AI supervisor mode';
  renderGraph();
}

/* ---------- rate-limit bars: live per-minute meters from API headers ---- */
const RL_METRICS = [['requests', 'Requests'], ['input-tokens', 'Input tok'],
  ['output-tokens', 'Output tok']];
function renderRateLimits(limits){
  const models = Object.keys(limits);
  if(!models.length){ $('#rl-bars').innerHTML = ''; return; }
  $('#rl-bars').innerHTML = RL_METRICS.map(([key, label]) => {
    // show the MOST CONSTRAINED model for this meter; all models on hover
    let worst = null, tips = [];
    for(const m of models){
      const v = limits[m][key];
      if(!v || !v.limit) continue;
      tips.push(`${m.replace('claude-','')}: ${v.remaining.toLocaleString()}`
        + ` of ${v.limit.toLocaleString()} left`);
      if(!worst || v.remaining / v.limit < worst.remaining / worst.limit)
        worst = v;
    }
    if(!worst) return '';
    const frac = worst.remaining / worst.limit;
    const color = frac > .5 ? 'var(--green)' : frac > .2
      ? 'var(--amber)' : 'var(--red)';
    return `<div class="rl-row" title="${esc(label)} per minute, per model — ${
      esc(tips.join(' · '))} (full bar = full budget available)">
      <span class="rl-top"><em>${label}/min</em>
      <b>${worst.remaining >= 1000
        ? fmtTok(worst.remaining) : worst.remaining} / ${
        worst.limit >= 1000 ? fmtTok(worst.limit) : worst.limit} left</b></span>
      <span class="rl-bar"><span style="width:${
        Math.max(2, Math.round(frac * 100))}%;background:${color}"></span></span>
      </div>`;
  }).join('');
}
setInterval(async () => {
  if(document.body.className === 'view-overview') return;  // Home already polls
  try{ sys = await (await fetch('/api/system')).json(); }catch(e){ return; }
  const api = sys.health.api || {};
  renderRateLimits(api.limits || {});
}, 30000);

/* ============== API Rate Tracking tab + queue footer line =============== */
let lastQueue = null;
const fmtAge = s => s == null ? '—' : s >= 60
  ? Math.floor(s/60) + 'm' + Math.round(s%60) + 's' : s.toFixed(1) + 's';
async function pollQueue(){
  try{ lastQueue = await (await fetch('/api/rate_limits')).json(); }
  catch(e){ return; }
  const q = lastQueue, c = q.counts;
  const busy = c.interactive_active + c.background_running;
  $('#dot-queue').className = 'dot ' + (q.waiting_capacity ? 'warn'
    : busy ? 'ok' : '');
  $('#txt-queue').textContent = q.waiting_capacity
    ? 'API queue: waiting for capacity'
    : busy || c.background_queued
      ? `API queue: ${busy} running · ${c.background_queued} queued`
      : 'API queue: idle';
  if(document.body.className === 'view-ratelimits') renderQueue();
}
setInterval(pollQueue, 3000);
pollQueue();

function qRow(e, live){
  const wait = e.capacity_waits
    ? ` <span class="m" title="times this call hit the rate limit and waited">(${
        e.capacity_waits}× capacity wait, ${Math.round(e.capacity_wait_s)}s)</span>` : '';
  return `<tr><td><span class="qc-pri ${e.priority}">${e.priority}</span></td>
    <td>${esc(e.label)}${wait}</td>
    <td class="mono">${esc((e.model||'').replace('claude-',''))}</td>
    <td><span class="qc-st ${e.state}">${
      e.state === 'waiting_capacity' ? 'waiting for API capacity' : e.state}</span>${
      e.error ? `<div class="m">${esc(e.error).slice(0,140)}</div>` : ''}</td>
    <td class="mono r">${live ? fmtAge(e.run_s ?? e.age_s)
      : fmtAge(e.run_s) + (e.queued_s > 0.5 ? ` (+${fmtAge(e.queued_s)} queued)` : '')}</td></tr>`;
}
function renderQueue(){
  const q = lastQueue;
  if(!q){ $('#ratelimits-body').innerHTML = 'Loading…'; return; }
  const c = q.counts;
  $('#ratelimits-body').innerHTML =
    (q.waiting_capacity ? `<div class="qc-banner">⚠ A live conversation is
      waiting for API capacity — background tests are throttled until it gets
      through.</div>` : '')
    + `<div class="qc-cards">
      <div class="qc-card"><div class="v">${c.interactive_active}</div>
        <div class="l">interactive running</div></div>
      <div class="qc-card"><div class="v">${c.background_running} / ${q.max_background}</div>
        <div class="l">background running</div></div>
      <div class="qc-card${c.background_queued ? ' warn' : ''}">
        <div class="v">${c.background_queued}</div>
        <div class="l">queued behind priority</div></div>
    </div>
    <div class="qc-sec"><div class="h">Live calls (interactive lane always goes
      first; background runs at most ${q.max_background} at a time and pauses
      while anyone is chatting)</div>
      <table><tr><th>Priority</th><th>Call</th><th>Model</th><th>State</th>
        <th class="r">Time</th></tr>${
        q.live.map(e => qRow(e, true)).join('')
        || '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:18px">No model calls in flight.</td></tr>'}</table></div>
    <div class="qc-sec"><div class="h">Recent calls</div>
      <table><tr><th>Priority</th><th>Call</th><th>Model</th><th>Outcome</th>
        <th class="r">Time</th></tr>${
        q.recent.map(e => qRow(e, false)).join('')
        || '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:18px">Nothing yet this session.</td></tr>'}</table></div>`;
}
function loadRateLimits(){ renderQueue(); pollQueue(); }

/* ================= Costs tab ================= */
const usd = v => v == null ? '—' : '$' + v.toFixed(v >= 10 ? 2 : 4);
async function loadBilling(){
  let d;
  try{ d = await (await fetch('/api/billing')).json(); }
  catch(e){ $('#billing-body').innerHTML = 'Failed to load costs.'; return; }
  const table = (title, cols, rows) => `<div class="qc-sec">
    <div class="h">${title}</div><table><tr>${
    cols.map((c,i) => `<th${i ? ' class="r"' : ''}>${c}</th>`).join('')}</tr>${
    rows.map(r => `<tr>${r.map((v,i) =>
      `<td${i ? ' class="mono r"' : ''}>${v}</td>`).join('')}</tr>`).join('')
    || `<tr><td colspan="${cols.length}" style="text-align:center;color:var(--muted);padding:18px">Nothing recorded yet.</td></tr>`}</table></div>`;
  const bucket = rows => rows.map(b =>
    [esc(b.name), usd(b.usd), b.calls, fmtTok(b.tokens)]);
  $('#billing-body').innerHTML = `
    <div class="qc-cards">
      <div class="qc-card"><div class="v">${usd(d.grand_total_usd)}</div>
        <div class="l">total spend (all recorded)</div></div>
      <div class="qc-card"><div class="v">${usd(d.production.total_usd)}</div>
        <div class="l">production (interviews, supervisor)</div></div>
      <div class="qc-card"><div class="v">${usd(d.lab.total_usd)}</div>
        <div class="l">simulation lab (${d.lab.tests.length} tests${
          d.lab.standalone_runs ? `, ${d.lab.standalone_runs} solo runs` : ''})</div></div>
    </div>
    <div class="m" style="color:var(--muted)">${esc(d.pricing_note)}${
      d.unpriced_models.length ? ` · unpriced models (tokens counted, $0 shown): ${
        esc(d.unpriced_models.join(', '))}` : ''}</div>
    ${table('Spend by day', ['Day','USD'],
      d.daily.map(x => [esc(x.day), usd(x.usd)]))}
    ${table('Production by purpose', ['Purpose','USD','Calls','Tokens'],
      bucket(d.production.by_purpose))}
    ${table('Production by model', ['Model','USD','Calls','Tokens'],
      bucket(d.production.by_model))}
    ${table('Production by project', ['Project','USD','Calls','Tokens'],
      bucket(d.production.by_project))}
    ${table('Lab tests', ['Test','USD','Status'],
      d.lab.tests.map(t => [`#${t.id} ${esc(t.kind)}`, usd(t.usd),
        esc(t.status)]))}`;
}

/* ---------- hierarchical orchestrator map ----------
   Two orchestrators: BotBot (platform) owns Chatbot Orchestrator, which owns
   the four bots. The focused orchestrator sits big in the middle; pressing a
   small outer orchestrator glides it to the middle, grows it, and its own
   child nodes emerge from it. Nodes are HTML circles so CSS transitions
   animate position/size; edges are redrawn after the glide settles. */
const TITLES = {requirements_bot:['Requirements','Bot'],
  architecture_agent:['Architect','Bot'], builder_agent:['Builder','Bot'],
  evaluation_agent:['Tester/Fixer','Bot']};

function taskLabel(n){
  return n === 0 ? 'Idle' : n === 1 ? '1 running task' : `${n} running tasks`;
}

let graphFocus = 'botbot';
const nodeEls = new Map();
let graphSig = '';
function renderGraph(){
  if(!sys) return;
  const wrap = $('#graph-wrap'), svg = $('#graph');
  const W = Math.max(560, wrap.clientWidth), H = Math.max(380, wrap.clientHeight);
  const sig = JSON.stringify([W, H, graphFocus, sys.active_runs.length,
    (sys.stats.project_states || {}).interviewing || 0,
    sys.capabilities.map(c => [c.id, !!c.enabled, !!c.planned, c.tasks || 0])]);
  if(sig === graphSig) return;
  graphSig = sig;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const cx = W/2, cy = H/2;
  let rC = 134, rN = 64, margin = 30;
  let ring = Math.min(W, H)/2 - rN - margin;
  const need = rC + rN + 46;
  if(ring < need){
    const k = Math.max(.5, ring/need);
    rC *= k; rN *= k;
    ring = Math.min(W, H)/2 - rN - margin;
  }
  const caps = sys.capabilities.filter(c => c.id !== 'ai_supervisor'
    && !c.hidden && c.kind !== 'orchestrator');
  const orchs = sys.capabilities.filter(c => c.kind === 'orchestrator');
  const nActive = sys.active_runs.length;
  // "tasks" = open interviews (a person mid-conversation counts as work in
  // progress even while the bot waits for their reply); the spinning ring
  // still marks a model call actually executing this second.
  const nTasks = (sys.stats.project_states || {}).interviewing || 0;
  const busy = nActive > 0;
  const oDot = busy ? 'var(--accent)' : 'var(--green)';
  // TASK ROLL-UP: every orchestrator's pill shows the TOTAL running tasks
  // of all nodes inside it, and the parent shows the sum over its children —
  // so Home answers "how much is going on?" without clicking in. A future
  // orchestrator (or a future bot inside one) contributes by adding its
  // count to its orchestrator's entry here; planned nodes contribute 0.
  const orchTasks = {chatbot: nTasks};   // chatbot = its bots' tasks summed
  for(const c of orchs) orchTasks[c.id] = c.planned ? 0 : (c.tasks || 0);
  const totalTasks = Object.values(orchTasks).reduce((a, b) => a + b, 0);
  const rollup = n => busy && n === 0 ? 'Working' : taskLabel(n);
  const specs = [];
  if(graphFocus === 'botbot'){
    specs.push({id:'botbot', title:['BotBot','Orchestrator'], x:cx, y:cy, r:rC,
      big:true, pill:rollup(totalTasks), dot:oDot,
      ring: busy ? 'center-active' : 'center-idle'});
    // BotBot's children share its ring: the live Chatbot Orchestrator plus
    // any planned orchestrators from the registry (e.g. Qualification).
    const kids = 1 + orchs.length;
    specs.push({id:'chatbot', title:['Chatbot','Orchestrator'], x:cx, y:cy-ring,
      r:rN, pill:rollup(orchTasks.chatbot), dot:oDot,
      ring: busy ? 'running' : 'idle'});
    orchs.forEach((c, i) => {
      const a = (-90 + (i+1)*360/kids) * Math.PI/180;
      specs.push({id:c.id, title:c.name.split(' '),
        x:cx + ring*Math.cos(a), y:cy + ring*Math.sin(a), r:rN,
        planned: !!c.planned,
        pill: c.planned ? 'Planned'
          : c.enabled ? taskLabel(orchTasks[c.id]) : 'Disabled',
        dot: c.planned ? 'var(--muted)'
          : c.enabled ? 'var(--green)' : 'var(--muted)',
        ring: c.planned ? null : (c.enabled ? 'idle' : null)});
    });
  } else {
    specs.push({id:'chatbot', title:['Chatbot','Orchestrator'], x:cx, y:cy, r:rC,
      big:true, pill:rollup(orchTasks.chatbot), dot:oDot,
      ring: busy ? 'center-active' : 'center-idle'});
    specs.push({id:'botbot', title:['BotBot','Orchestrator'], x:cx, y:cy-ring,
      r:rN, pill:rollup(totalTasks), dot:oDot, ring: busy ? 'running' : 'idle'});
    caps.forEach((c, i) => {
      const a = (-90 + (i+1)*360/(caps.length+1)) * Math.PI/180;
      const x = cx + ring*Math.cos(a), y = cy + ring*Math.sin(a);
      const isReq = c.id === 'requirements_bot';
      const working = isReq && busy;
      specs.push({id:c.id, title:TITLES[c.id]||[c.name], x, y, r:rN,
        planned: !!c.planned,
        pill: c.planned ? 'Planned'
          : isReq ? taskLabel(nTasks) : (c.enabled ? 'Idle' : 'Disabled'),
        dot: c.planned ? 'var(--muted)' : working ? 'var(--accent)'
          : c.enabled ? 'var(--green)' : 'var(--muted)',
        ring: c.planned ? null : working ? 'running' : (c.enabled ? 'idle' : null)});
    });
  }
  // edges appear once the glide settles
  const center = specs[0], others = specs.slice(1);
  clearTimeout(renderGraph._et);
  svg.innerHTML = '';
  renderGraph._et = setTimeout(() => {
    svg.innerHTML = others.map(s => {
      const dx = s.x-center.x, dy = s.y-center.y, d = Math.hypot(dx,dy)||1;
      const active = s.id === 'requirements_bot' && busy;
      return `<line class="gedge fadein${active?' active':''}${s.planned?' planned':''}"
        x1="${center.x+dx/d*center.r}" y1="${center.y+dy/d*center.r}"
        x2="${s.x-dx/d*s.r}" y2="${s.y-dy/d*s.r}"/>`;
    }).join('');
  }, 540);
  // nodes: persistent divs so CSS transitions animate the moves
  const seen = new Set();
  for(const s of specs){
    seen.add(s.id);
    let el = nodeEls.get(s.id);
    if(!el){
      el = document.createElement('div');
      el.className = 'hnode';
      el.tabIndex = 0;
      el.setAttribute('role', 'button');
      el.innerHTML = '<span class="nring"></span><span class="ttl"></span>'
        + '<span class="npill"></span>';
      wrap.appendChild(el);
      nodeEls.set(s.id, el);
      // emerge from the center of the focused node
      el.style.left = (center.x-16)+'px'; el.style.top = (center.y-16)+'px';
      el.style.width = '32px'; el.style.height = '32px'; el.style.opacity = '0';
      void el.offsetWidth;
    }
    el.classList.toggle('planned', !!s.planned);
    el.style.left = (s.x-s.r)+'px'; el.style.top = (s.y-s.r)+'px';
    el.style.width = (2*s.r)+'px'; el.style.height = (2*s.r)+'px';
    el.style.opacity = s.planned ? '.65' : '1';
    const ttl = el.querySelector('.ttl');
    ttl.style.fontSize = (s.big ? 25 : 15) + 'px';
    ttl.innerHTML = s.title.map(esc).join('<br>');
    el.querySelector('.npill').innerHTML =
      `<span class="dot" style="color:${s.dot}">●</span> ${esc(s.pill)}`;
    el.querySelector('.nring').className = 'nring' + (s.ring ? ' '+s.ring : '');
    el.setAttribute('aria-label', s.title.join(' ') + ' — ' + s.pill);
    el.onclick = () => {
      if(s.id === 'botbot' || s.id === 'chatbot'){
        if(graphFocus !== s.id){ graphFocus = s.id; graphSig = ''; renderGraph(); }
        else openOverlay('#sup-drawer', '#mgmt-input');
      } else if(s.id === 'requirements_bot') showView('pipelines');
    };
    el.onkeydown = e => {
      if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); el.onclick(); }
    };
  }
  // exiting nodes retreat into the center and fade
  for(const [id, el] of [...nodeEls]){
    if(seen.has(id)) continue;
    nodeEls.delete(id);
    el.style.left = (center.x-10)+'px'; el.style.top = (center.y-10)+'px';
    el.style.width = '20px'; el.style.height = '20px'; el.style.opacity = '0';
    setTimeout(() => el.remove(), 540);
  }
  // New Session action rides under the Requirements Bot node
  const rspec = specs.find(s => s.id === 'requirements_bot');
  let nb = document.getElementById('new-session-btn');
  if(rspec){
    if(!nb){
      nb = document.createElement('button');
      nb.id = 'new-session-btn';
      nb.className = 'hbtn';
      nb.textContent = '+ New Session';
      nb.setAttribute('aria-label', 'Start a new interview session');
      nb.onclick = e => { e.stopPropagation(); window.open('/chat?new=1', '_blank'); };
      wrap.appendChild(nb);
      nb.style.opacity = '0';
      void nb.offsetWidth;
    }
    nb.style.left = rspec.x+'px';
    nb.style.top = (rspec.y+rspec.r+22)+'px';
    nb.style.opacity = '1';
  } else if(nb){
    nb.style.opacity = '0';
    setTimeout(() => { if(graphFocus === 'botbot') nb.remove(); }, 540);
  }
}
let rsz;
window.addEventListener('resize', () => { clearTimeout(rsz); rsz = setTimeout(renderGraph, 120); });

/* ---------- overlays (supervisor + attention) ---------- */
function openOverlay(sel, focusSel){
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('open'));
  $(sel).classList.add('open');
  $('#sup-fab').setAttribute('aria-expanded', String(sel === '#sup-drawer'));
  if(sel === '#sup-drawer') loadMgmt();
  if(sel === '#att-drawer') loadAttention();
  if(focusSel) $(focusSel).focus();
}
function closeOverlays(){
  document.querySelectorAll('.overlay').forEach(o => o.classList.remove('open'));
  $('#sup-fab').setAttribute('aria-expanded', 'false');
}
$('#sup-fab').onclick = () =>
  $('#sup-drawer').classList.contains('open') ? closeOverlays()
    : openOverlay('#sup-drawer', '#mgmt-input');
$('#sup-close').onclick = closeOverlays;
$('#att-close').onclick = closeOverlays;
document.addEventListener('keydown', e => {
  if(e.key === 'Escape'){ closeOverlays(); $('#viewer').classList.remove('open'); }
});

async function loadAttention(){
  let items = [];
  try{ items = await (await fetch('/api/attention')).json(); }catch(e){}
  $('#att-list').innerHTML = (items && items.length)
    ? items.map(rv => `<div class="att-item">
        <span class="blk b${rv.blocking?1:0}">${rv.blocking?'blocking':'review'}</span>
        <span class="txt">${esc(rv.decision_needed)}
          <div class="meta">${esc(rv.project_name||rv.project_id)} · rev ${rv.revision??'—'} · #${rv.id}</div></span>
        <button class="act" data-rid="${rv.id}" data-pid="${rv.project_id}">Resolve…</button>
      </div>`).join('')
    : `<div class="att-empty">No open alerts or review requests.</div>`;
  document.querySelectorAll('#att-list [data-rid]').forEach(b => b.onclick = async () => {
    const disp = prompt('Disposition (what was decided and why):');
    if(!disp) return;
    await fetch('/api/review/resolve', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project: b.dataset.pid, review_id: +b.dataset.rid, disposition: disp})});
    loadAttention(); loadSystem();
  });
}

/* ---------- supervisor management chat (SYSTEM scope) ---------- */
let mgmtCount = -1;
async function loadMgmt(){
  try{
    const d = await (await fetch('/api/management?scope=system')).json();
    const n = (d.messages||[]).length;
    if(n === mgmtCount) return;
    mgmtCount = n;
    renderMgmt(d);
  }catch(e){}
}
function renderMgmt(d){
  const t = $('#mgmt-thread');
  t.innerHTML = (d.messages||[]).map(m =>
    `<div class="mmsg ${m.role}"><div class="who">${m.role}</div>${esc(m.text)}</div>`).join('')
    || `<div class="mmsg system">System scope: ask what is running, which runs failed, or what awaits review. The supervisor reads recorded state only and cannot change anything.</div>`;
  t.scrollTop = t.scrollHeight;
}
async function sendMgmt(){
  const inp = $('#mgmt-input'), text = inp.value.trim();
  if(!text) return;
  inp.value = '';
  const t = $('#mgmt-thread');
  t.insertAdjacentHTML('beforeend',
    `<div class="mmsg operator"><div class="who">operator</div>${esc(text)}</div>
     <div class="mmsg system" id="mgmt-wait">supervisor is answering…</div>`);
  t.scrollTop = t.scrollHeight;
  try{
    const d = await (await fetch('/api/management', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({scope: 'system', text})})).json();
    mgmtCount = (d.messages||[]).length;
    renderMgmt(d);
  }catch(e){
    document.getElementById('mgmt-wait')?.remove();
    t.insertAdjacentHTML('beforeend', `<div class="mmsg system">request failed</div>`);
  }
}
$('#mgmt-send').onclick = sendMgmt;
$('#mgmt-input').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendMgmt(); }
});
$('#sup-toggle').onclick = async () => {
  const enabling = (sys && sys.health.supervisor_mode) !== 'advisory';
  if(enabling && !confirm('Enable the AI supervisor (advisory mode)? Each question you ask it becomes a paid model call.')) return;
  await fetch('/api/supervisor/enable', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({enabled: enabling})});
  loadSystem();
};

/* (owner test chat + copy link removed from sidebar) */

/* ================= Simulation Lab ================= */
const simUI = {runs: [], tests: [], expanded: new Set(), mode: 'training'};
async function loadFeatureCatalog(){
  if(loadFeatureCatalog.done) return;
  try{
    const cat = await (await fetch('/api/sim/feature_catalog')).json();
    const box = $('#simt-featchips');
    const groups = [
      ['Schema fields (from the requirements form)', cat.schema_fields],
      ['Industries', cat.industries],
      ['Hardest ladder rungs', cat.ladders],
      ['Business setups', cat.categories],
      ['Special behaviors', cat.behaviors]];
    box.innerHTML = groups.map(([label, items]) => `
      <div class="fgroup"><div class="fg-t">${esc(label)}</div>
      <div class="fg-c">${(items || []).map(v => `
        <span class="fchip" data-v="${esc(v)}" role="checkbox"
          aria-checked="false" tabindex="0">${
          esc(v.replace('schema_field=', '').replace('=', ': '))}</span>`)
        .join('')}</div></div>`).join('');
    box.querySelectorAll('.fchip').forEach(c => {
      const flip = () => {
        c.classList.toggle('on');
        c.setAttribute('aria-checked', c.classList.contains('on'));
      };
      c.onclick = flip;
      c.onkeydown = e => {
        if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); flip(); }
      };
    });
    loadFeatureCatalog.done = true;
  }catch(e){}
}
/* Two labs, two tabs: Training (sweeps, stress, regression) and Testing
   (feature checks). Same engine and archive underneath — each tab shows its
   own form and only its own tests. */
function openSimTab(mode){
  simUI.mode = mode;
  simUI.sig = '';   // the tests list filters by tab — force a re-render
  if(mode === 'feature_check') loadFeatureCatalog();
  const feat = mode === 'feature_check';
  $('#simt-mode-label').textContent = feat
    ? 'Feature Testing' : 'Requirement Bot Training';
  $('#simt-mode-desc').textContent = feat
    ? 'One persona per ticked feature, nothing else defined — each feature '
      + 'its own parallel test with a strict YES/NO verdict.'
    : 'Persona sweeps, stress tests and regression pins — batches of fake '
      + 'businesses interview the bot, an analyst finds problems and drafts '
      + 'fixes.';
  $('#simt-kind').onchange();
  $('#simt-form').classList.add('open');
  loadSim();
}
$('#simt-kind').onchange = () => {
  const feat = simUI.mode === 'feature_check';
  const k = feat ? 'feature_check' : $('#simt-kind').value;
  // Feature Testing has exactly one kind, so its dropdown is noise: only
  // the tickable feature catalog shows.
  $('#simt-f-kind').style.display = feat ? 'none' : '';
  $('#simt-f-count').style.display =
    (k === 'regression_pin' || k === 'feature_check') ? 'none' : '';
  $('#simt-f-stress').style.display = k === 'stress' ? '' : 'none';
  $('#simt-f-base').style.display = k === 'regression_pin' ? '' : 'none';
  $('#simt-f-features').style.display = feat ? '' : 'none';
};
$('#simt-start').onclick = async () => {
  const kind = simUI.mode === 'feature_check'
    ? 'feature_check' : $('#simt-kind').value;
  const body = {kind};
  if(kind === 'feature_check'){
    body.features = [...document.querySelectorAll('#simt-featchips .fchip.on')]
      .map(c => c.dataset.v);
    if(!body.features.length) return;
  } else if(kind === 'regression_pin'){
    body.base_test_id = +$('#simt-base').value || 0;
    if(!body.base_test_id) return;
  } else {
    body.count = Math.max(2, Math.min(8, +$('#simt-count').value || 4));
    if(kind === 'stress'){
      body.dimension = $('#simt-dim').value;
      body.level = +$('#simt-level').value;
    }
  }
  await fetch('/api/sim/test', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  loadSim();
};
const SIMT_STEPS = [
  ['starting', 'Start'], ['generating', 'Generate Personas'],
  ['running', 'Run Interviews'], ['analyzing', 'Analyze'],
  ['completed', 'Report Ready']];
const FBAR_COLORS = ['#61afef','#98c379','#e5c07b','#c678dd','#56b6c2',
  '#d19a66','#e06c75','#7f848e'];
const TRAIT_WORDS = ['impatient','patient','chatty','terse','tech-savvy',
  'technophobe','suspicious','trusting','analytical','formal','casual',
  'friendly','anxious','skeptical','precise'];

function sizeBucket(s){
  if(!s) return null;
  const t = String(s).toLowerCase();
  if(/solo|alone|just me|owner only|1 person|one person/.test(t)) return 'solo';
  const m = t.match(/\d+/);
  const n = m ? +m[0] : null;
  if(n == null) return t.slice(0, 18);
  if(n <= 1) return 'solo';
  if(n <= 5) return '2–5 staff';
  if(n <= 15) return '6–15 staff';
  return '16+ staff';
}
function count(map, key){ if(key) map.set(key, (map.get(key)||0)+1); }
function ladderBucket(v, kind){
  if(v == null) return null;
  const p = Math.round(v * 100);
  if(kind === 'know')
    return p >= 90 ? 'knows everything' : p >= 65 ? 'knows most'
      : p >= 45 ? 'knows half' : 'knows little';
  return p >= 80 ? 'very patient' : p >= 50 ? 'cooperative'
    : p >= 25 ? 'impatient' : 'wants it over';
}
function simAgg(runs){
  const industry = new Map(), size = new Map(), traits = new Map(),
    outcome = new Map(), know = new Map(), pat = new Map(),
    langs = new Map(), chans = new Map(), beh = new Map(),
    xl = {}, xc = {};
  for(const r of runs){
    count(industry, r.industry);
    count(size, sizeBucket(r.company_size));
    count(know, ladderBucket(r.completeness, 'know'));
    count(pat, ladderBucket(r.patience, 'pat'));
    const t = (r.traits||'').toLowerCase();
    for(const w of TRAIT_WORDS)
      if(new RegExp('\\b' + w + '\\b').test(t)) count(traits, w);
    if(/language|arabic|spanish|mixes/.test(t)) count(traits, 'mixes languages');
    const f = r.features || {};
    for(const [n, v] of Object.entries(f.ladders || {}))
      count(xl[n] = xl[n] || new Map(), Math.round(v*100)+'%');
    for(const n of ['business_model','data_situation','requested_scope',
                    'decision_structure'])
      if(f[n]) count(xc[n] = xc[n] || new Map(), f[n]);
    if(f.behavior && f.behavior !== 'none') count(beh, f.behavior);
    const id = r.identity || {};
    const asList = v => Array.isArray(v) ? v : (v == null ? [] : [v]);
    for(const l of asList(id.languages)) count(langs, String(l).slice(0, 16));
    for(const c of asList(id.channels)) count(chans, String(c).slice(0, 18));
    count(outcome, r.status === 'running' ? 'running'
      : r.status === 'failed' ? 'failed'
      : r.interview_complete ? 'completed' : 'incomplete');
  }
  return {industry, size, traits, outcome, know, pat, langs, chans,
    beh, xl, xc};
}
const XL_LABELS = {consistency:'Consistency', clarity:'Clarity',
  trust:'Trust', language_mix:'Language mix', typing:'Typing quality',
  focus:'Focus'};
const XC_LABELS = {business_model:'Business model',
  data_situation:'Data situation', requested_scope:'Requested scope',
  decision_structure:'Decision structure'};
function fbar(title, map, denom, overlapping){
  if(!map.size) return '';
  const entries = [...map.entries()].sort((x, y) => y[1] - x[1]);
  const total = entries.reduce((s, e) => s + e[1], 0);
  const segs = entries.map(([k, v], i) =>
    `<span style="flex:${v};background:${FBAR_COLORS[i % 8]}"></span>`).join('');
  const lbls = entries.map(([k, v]) =>
    `${esc(k)} ${Math.round(v / denom * 100)}%`).join(' · ');
  return `<div class="fbar"><em>${title}${overlapping
      ? ' <span class="fo">(overlapping)</span>' : ''}</em>
    <div class="fb">${segs}</div><div class="fls">${lbls}</div></div>`;
}
function usageCost(u, fam){
  if(!u) return null;
  const m = fam || u.model || 'sonnet';
  const p = /haiku/.test(m) ? [1.0, 5.0] : /opus/.test(m) ? [5.0, 25.0]
    : [2.0, 10.0];
  return ((u.fresh_in||0)*p[0] + (u.cache_write||0)*p[0]*2
    + (u.cache_read||0)*p[0]*0.10 + (u.out||0)*p[1]) / 1e6;
}
function simRunRow(r){
  const u = r.usage || {}, bu = u.bot || {}, pu = u.persona || {};
  const dur = r.finished_ts ? fmtSecs(r.finished_ts - r.started_ts)
    : fmtSecs(Date.now()/1000 - r.started_ts);
  const st = r.status === 'running'
    ? '<span class="sim-st running"><span class="spin"></span> interviewing</span>'
    : r.status === 'failed'
      ? `<span class="sim-st failed" title="${esc(r.error||'')}">failed</span>`
      : `<span class="sim-st completed">${r.interview_complete
          ? 'completed ✓' : 'incomplete'}</span>`;
  return `<tr>
    <td style="color:var(--text)">${esc(r.persona_name)}</td>
    <td>${esc(r.industry || '—')}</td>
    <td>${esc(sizeBucket(r.company_size) || '—')}</td>
    <td class="mono">${r.completeness != null
      ? Math.round(r.completeness*100)+'%' : '—'}</td>
    <td class="mono">${r.patience != null
      ? Math.round(r.patience*100)+'%' : '—'}</td>
    <td class="mono" ${r.missed && r.missed.length ? `title="Missed: ${
      esc(r.missed.join(', '))}"` : ''}>${r.score != null
      ? `<span style="color:${r.score >= .8 ? 'var(--green)'
          : r.score >= .5 ? 'var(--amber)' : 'var(--red)'}">${
          Math.round(r.score*100)}%</span>` : '—'}</td>
    <td>${st}</td>
    <td class="mono">${r.turns ?? 0}</td>
    <td class="mono">${r.cost_usd != null ? '$'+r.cost_usd.toFixed(2) : '—'}</td>
    <td class="mono">${bu.out != null ? '$'+(usageCost(bu)||0).toFixed(2) : '—'}</td>
    <td class="mono">${pu.out != null
      ? '$'+(usageCost(pu, 'haiku')||0).toFixed(2) : '—'}</td>
    <td class="mono">${fmtTok((bu.fresh_in||0)+(bu.cache_read||0)+(bu.cache_write||0))}</td>
    <td class="mono">${fmtTok(bu.out||0)}</td>
    <td class="mono">${fmtTok(bu.cache_read||0)}</td>
    <td class="mono">${fmtTok(bu.cache_write||0)}</td>
    <td class="mono">${dur}</td>
    <td style="white-space:nowrap"><button class="act" onclick="event.stopPropagation();simView(${r.id},'identity')">Identity</button>
      <button class="act" onclick="event.stopPropagation();simView(${r.id},'transcript')">Input</button>
      ${r.has_brief ? `<button class="act" onclick="event.stopPropagation();simView(${r.id},'brief')">Output</button>` : ''}</td>
  </tr>`;
}
function simtCard(t){
  const order = SIMT_STEPS.map(s => s[0]);
  const failed = t.status === 'failed';
  const idx = failed ? order.length : order.indexOf(t.status);
  const runs = t.run_ids.map(id => simUI.runs.find(r => r.id === id))
    .filter(Boolean);
  const doneRuns = runs.filter(r => r.status !== 'running').length;
  const open = simUI.expanded.has(t.id);
  // per-stage costs, visible live under each milestone
  const u = t.usage || {};
  const runsCost = runs.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const stageCost = {
    generating: usageCost(u.generator),
    running: runs.length ? runsCost : null,
    analyzing: usageCost(u.analyst),
  };
  const liveTotal = t.cost_usd != null ? t.cost_usd
    : (stageCost.generating || 0) + runsCost + (stageCost.analyzing || 0);
  // per-milestone detail boxes (same idea as the Workflows interview box)
  const short = m => (m || '').replace('claude-', '') || '—';
  const models = t.params.models || {};
  const sumB = k => runs.reduce((s, r) => s + (((r.usage||{}).bot||{})[k]||0), 0);
  const totTurns = runs.reduce((s, r) => s + (r.turns||0), 0);
  const kv = rows => `<span class="simt-det">${rows.filter(r => r).map(
    ([k, v]) => `<span class="sdk"><span>${k}</span><b>${v}</b></span>`)
    .join('')}</span>`;
  const stageDet = {
    generating: u.generator ? kv([
      ['Model', esc(short(u.generator.model))],
      ['Personas', t.run_ids.length || t.params.count],
      ['In · Out', `${fmtTok(u.generator.fresh_in||0)} · ${
        fmtTok(u.generator.out||0)}`],
      stageCost.generating != null
        && ['Cost', '$'+stageCost.generating.toFixed(2)]]) : '',
    running: runs.length ? kv([
      ['Interviews', `${doneRuns}/${t.run_ids.length}`],
      models.bot && ['Bot model', esc(short(models.bot))],
      models.persona && ['Persona model', esc(short(models.persona))],
      ['Turns', totTurns],
      ['In · Out', `${fmtTok(sumB('fresh_in')+sumB('cache_read')
        +sumB('cache_write'))} · ${fmtTok(sumB('out'))}`],
      ['Cache R · W', `${fmtTok(sumB('cache_read'))} · ${
        fmtTok(sumB('cache_write'))}`],
      ['Cost', '$'+runsCost.toFixed(2)]]) : '',
    analyzing: u.analyst ? kv([
      ['Model', esc(short(u.analyst.model))],
      ['In · Out', `${fmtTok(u.analyst.fresh_in||0)} · ${
        fmtTok(u.analyst.out||0)}`],
      stageCost.analyzing != null
        && ['Cost', '$'+stageCost.analyzing.toFixed(2)]]) : '',
    completed: t.has_report ? `<span class="simt-det"><button class="act"
      onclick="event.stopPropagation();simReport(${t.id})"
      style="margin-top:4px">View Report</button></span>` : '',
  };
  const steps = SIMT_STEPS.slice(1).map(([key, label], i) => {
    const pos = i + 1;
    const cls = failed ? 'fail'
      : pos < idx || t.status === 'completed' ? 'done'
      : pos === idx ? 'cur' : '';
    const st = cls === 'done' ? 'Completed'
      : cls === 'fail' ? 'Failed'
      : cls === 'cur'
        ? (key === 'running'
            ? `<span class="spin"></span> ${doneRuns}/${t.run_ids.length || t.params.count}`
            : '<span class="spin"></span> Running')
        : 'Pending';
    return `<div class="simt-step ${cls}"><span class="cn">${
      cls === 'done' ? '✓' : pos}</span><span class="lbl">${label}</span>
      <span class="st">${st}</span>${stageDet[key] || ''}</div>`;
  }).join('');
  const agg = simAgg(runs);
  const n = runs.length || 1;
  const scored = runs.filter(r => r.score != null);
  const avgScore = scored.length
    ? Math.round(scored.reduce((s, r) => s + r.score, 0) / scored.length * 100)
    : null;
  const bars = runs.length ? `<div class="fbars">
      ${fbar('Industry', agg.industry, n, false)}
      ${fbar('Company size', agg.size, n, false)}
      ${fbar('Owner knowledge', agg.know, n, false)}
      ${fbar('Patience', agg.pat, n, false)}
      ${Object.entries(agg.xl).map(([k, m]) =>
        fbar(XL_LABELS[k] || k, m, n, false)).join('')}
      ${Object.entries(agg.xc).map(([k, m]) =>
        fbar(XC_LABELS[k] || k, m, n, false)).join('')}
      ${fbar('Special behavior', agg.beh, n, true)}
      ${fbar('Personality', agg.traits, n, true)}
      ${fbar('Languages', agg.langs, n, true)}
      ${fbar('Channels wanted', agg.chans, n, true)}
    </div>` : '';
  const table = open && runs.length ? `<div class="simt-table">
    <table><tr><th>Persona</th><th>Industry</th><th>Size</th><th>Knows</th>
      <th>Patience</th><th>Score</th><th>Status</th><th>Turns</th>
      <th>Total Cost</th><th>Bot Cost</th><th>Persona Cost</th>
      <th>Input Tokens</th><th>Output Tokens</th><th>Cache Read</th>
      <th>Cache Write</th><th>Duration</th><th></th></tr>
      ${runs.map(simRunRow).join('')}</table></div>` : '';
  return `<div class="simt-card${open ? ' open' : ''}" data-tid="${t.id}">
    <div class="simt-top" role="button" tabindex="0" aria-expanded="${open}">
      <span class="n">Test ${t.id} · ${t.kind === 'stress'
        ? `Stress Test (${esc(t.params.dimension||'')} pinned at ${
            Math.round((t.params.level||0)*100)}%, ${t.params.count} personas)`
        : t.kind === 'regression_pin'
        ? `Regression vs Test ${t.params.base_test_id}`
        : t.kind === 'feature_check'
        ? `Feature Check · ${esc(t.params.feature||'')}`
        : `Persona Sweep &amp; Analysis (${t.params.count} personas)`}</span>
      ${t.params.verdict ? `<span class="sim-st ${t.params.verdict === 'YES'
        ? 'completed' : 'failed'}" style="font-size:12px">${
        t.params.verdict}</span>` : ''}
      <span class="ts">${new Date(t.started_ts*1000).toLocaleString()}</span>
      ${avgScore != null ? `<span class="ts" style="color:var(--green);
        font-weight:600" title="Average extraction score: of what each persona
        actually knew, how much the interview captured">score ${avgScore}%</span>` : ''}
      <span class="cost">${t.cost_usd != null ? '$'+t.cost_usd.toFixed(2)
        : liveTotal ? '~$'+liveTotal.toFixed(2) : ''}</span>
      <span class="chev">${open ? '▾' : '▸'}</span></div>
    ${Object.keys(models).length ? `<span class="sim-meta"
      style="margin-top:8px" title="The model line-up this test ran on — a
      registered dimension, so the same feature mix can be compared across
      models between tests"><span>${['generator','bot','persona','analyst']
      .filter(k => models[k]).map(k =>
        `<span style="color:var(--muted)">${k}:</span> ${
          esc(short(models[k]))}`).join(' · ')}</span></span>` : ''}
    <div class="simt-path">${steps}</div>
    ${bars}
    ${table}
    <div class="simt-actions">
      ${t.status === 'completed' ? `<button class="act"
        onclick="fetch('/api/sim/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'persona_sweep',count:${t.params.count}})}).then(loadSim)"
        title="Run a fresh sweep of the same size against the current bot">Re-run</button>` : ''}
      ${t.error ? `<span class="m" style="color:var(--red)">${esc(t.error)}</span>` : ''}
    </div></div>`;
}
async function simReport(id){
  const d = await (await fetch('/api/sim/test_report?id='+id)).json();
  $('#viewer-title').textContent = `Test ${id} — analysis report`;
  $('#viewer-dl').innerHTML = '';
  $('#viewer-pre').textContent = d.report || '(no report)';
  $('#viewer').classList.add('open');
}
async function simView(id, what){
  const d = await (await fetch('/api/sim/run_detail?id='+id)).json();
  $('#viewer-dl').innerHTML = '';
  if(what === 'identity'){
    $('#viewer-title').textContent = `Run #${id} — ground-truth identity · ${d.persona_name}`;
    $('#viewer-pre').textContent = d.persona_content || '(no identity)';
    $('#viewer').classList.add('open');
    return;
  }
  if(what === 'transcript'){
    $('#viewer-title').textContent = `Run #${id} — conversation · ${d.persona_name}`;
    $('#viewer-pre').textContent = (d.transcript||[]).map(t =>
      (t.who === 'bot' ? 'RequirementBot:  ' : 'Persona:         ') + t.text)
      .join('\n\n');
  } else {
    $('#viewer-title').textContent = `Run #${id} — final brief · ${d.persona_name}`;
    $('#viewer-pre').textContent = d.brief
      ? JSON.stringify(d.brief, null, 2) : '(no brief — run did not complete)';
  }
  $('#viewer').classList.add('open');
}
async function loadSim(){
  try{
    [simUI.runs, simUI.tests] = await Promise.all([
      (await fetch('/api/sim/runs')).json(),
      (await fetch('/api/sim/tests')).json()]);
  }catch(e){ return; }
  const feat = simUI.mode === 'feature_check';
  // Each lab tab lists only its own tests (one shared archive underneath).
  const shown = simUI.tests.filter(t => (t.kind === 'feature_check') === feat);
  const sig = JSON.stringify([simUI.mode, shown, simUI.runs.map(r =>
    [r.id, r.status, r.turns]), [...simUI.expanded]]);
  if(sig === simUI.sig) return;   // nothing changed — don't disturb clicks
  simUI.sig = sig;
  // NEW-FEATURE BUBBLE (Testing Lab only): schema fields that exist in the
  // requirements form but have never been feature-tested get flagged for
  // one-click testing
  try{
    if(!feat) $('#sim-newfeat').style.display = 'none';
    else{
    if(!simUI.catalog)
      simUI.catalog = await (await fetch('/api/sim/feature_catalog')).json();
    const tested = new Set(simUI.tests
      .filter(t => t.kind === 'feature_check' && t.params.feature)
      .map(t => 'schema_field=' + t.params.feature.split('=').pop().trim()));
    const fresh = (simUI.catalog.schema_fields || []).filter(f => {
      const norm = 'schema_field=' + f.split('=').pop().trim();
      return !tested.has(norm);
    });
    const box = $('#sim-newfeat');
    if(fresh.length){
      box.style.display = 'flex';
      box.innerHTML = `<b>${fresh.length} schema feature${
        fresh.length === 1 ? '' : 's'} never tested</b>
        <span>${esc(fresh.slice(0, 6).map(f =>
          f.replace('schema_field=', '')).join(', '))}${
          fresh.length > 6 ? '…' : ''}</span>
        <button class="act" style="margin-left:auto" id="sim-newfeat-add">
          Add &amp; test ${fresh.length}</button>`;
      $('#sim-newfeat-add').onclick = async () => {
        box.style.display = 'none';
        await fetch('/api/sim/test', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({kind: 'feature_check', features: fresh})});
        loadSim();
      };
    } else box.style.display = 'none';
    }
  }catch(e){}
  $('#sim-tests').innerHTML = shown.map(simtCard).join('')
    || (feat
      ? `<div class="empty-state"><b>No feature tests yet</b>
          <span>Tick features above and start — each ticked feature runs as
          its own parallel test with a YES/NO verdict, archived here
          permanently.</span></div>`
      : `<div class="empty-state"><b>No training tests yet</b>
          <span>Start test runs a batch of fake businesses through the bot and
          analyzes every input and output for problems and fixes. Every test,
          conversation, and brief is archived here permanently.</span></div>`);
  document.querySelectorAll('.simt-card .simt-top').forEach(h => {
    const tid = +h.parentElement.dataset.tid;
    const toggle = () => {
      simUI.expanded.has(tid) ? simUI.expanded.delete(tid)
        : simUI.expanded.add(tid);
      loadSim();
    };
    h.onclick = toggle;
    h.onkeydown = e => {
      if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); toggle(); }
    };
  });
}

/* ================= Clients ================= */
async function loadCustomers(){
  let clients = [];
  try{ clients = await (await fetch('/api/customers')).json(); }
  catch(e){ return; }
  $('#customers-body').innerHTML = clients.length
    ? `<table><tr><th>Client ID</th><th>Name</th><th>Business</th>
        <th>Project</th><th>State</th><th>Messages</th><th>Registered</th>
        <th>Last seen</th></tr>` + clients.map(c => `<tr>
        <td class="cid">Client ${c.client_id}</td>
        <td class="cname">${esc(c.contact_name || '—')}</td>
        <td class="cname">${esc(c.business_name || '—')}</td>
        <td class="cmono">#${c.project_num ?? '—'}</td>
        <td>${esc(c.state || '—')}${c.interview_complete ? ' ✓' : ''}</td>
        <td class="cmono">${c.msgs ?? 0}</td>
        <td class="cmono">${c.created_ts
          ? new Date(c.created_ts*1000).toLocaleString() : '—'}</td>
        <td class="cmono">${c.last_seen_ts ? ago(c.last_seen_ts) : '—'}</td>
      </tr>`).join('') + `</table>`
    : `<div class="empty-state"><b>No clients registered yet</b>
       <span>Clients are registered automatically when they start a
       conversation through the client link.</span></div>`;
}

function renderActivityTable(events){
  const rows = events.map(e => {
    const d = new Date(e.ts*1000);
    const t = `${d.toLocaleDateString('en-CA')} ${d.toLocaleTimeString('en-GB')}`;
    const proj = e.project_id === '__system__' ? 'system'
      : !e.project_id ? 'console' : `#${e.project_num ?? '?'}`;
    const client = e.client_id != null ? `Client${e.client_id}` : '—';
    if(e.kind === 'msg'){
      const dir = e.role === 'owner' ? 'Client → Bot' : 'Bot → Client';
      return `<tr><td class="cmono">${t}</td><td class="cmono">${esc(proj)}</td>
        <td class="cmono">${esc(client)}</td><td>Message</td>
        <td>${dir}</td><td>${esc(e.text.slice(0, 160))}${
        e.text.length > 160 ? '…' : ''}</td></tr>`;
    }
    const detail = Object.entries(e.payload||{})
      .map(([k,v]) => `${k}=${typeof v==='object'?JSON.stringify(v):v}`).join(' · ');
    const actor = (e.actor === 'client' || e.actor === 'owner')
      && e.client_id != null ? `Client${e.client_id}` : e.actor;
    return `<tr><td class="cmono">${t}</td><td class="cmono">${esc(proj)}</td>
      <td class="cmono">${esc(client)}</td><td>Event</td>
      <td class="cmono">${esc(e.type)} (${esc(actor)})</td>
      <td>${esc(detail.slice(0, 160))}</td></tr>`;
  }).join('');
  $('#activity-body').innerHTML = `<table class="activity-table"><tr><th>Time</th>
    <th>Project</th><th>Client</th><th>Kind</th><th>What</th>
    <th>Details</th></tr>${rows
    || '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">Nothing matches the filters.</td></tr>'}</table>`;
}

/* ================= Live Log (system-wide event feed) ================= */
let logSig = '';
const logFilter = {proj: '', client: '', bot: '', kind: ''};
// Project/client are TYPE-AHEAD prefix filters: type 8 and every number
// starting with 8 stays; type 84 and it narrows to 84, 842, ... Digits only.
for(const [id, key] of [['#lf-proj','proj'], ['#lf-client','client']]){
  $(id).oninput = e => {
    e.target.value = e.target.value.replace(/\D/g, '');
    logFilter[key] = e.target.value; logSig = ''; loadActivity();
  };
}
$('#lf-bot').onchange = e => {
  logFilter.bot = e.target.value; logSig = ''; loadActivity();
};
const LOG_CATS = {
  conversations: e => e.kind === 'msg',
  clicks: e => e.type === 'ui.click',
  milestones: e => ['project.created','materials.uploaded','interview.closed',
    'readiness.evaluated','review.resolved','approval.recorded',
    'approval.invalidated','export.created'].includes(e.type),
  attention: e => ['review.requested','interview.reset'].includes(e.type),
  failures: e => /fail|error/.test(e.type || ''),
};
function matchKind(e){
  const v = logFilter.kind;
  if(!v) return true;
  const [k, x] = v.split(':');
  if(k === 'cat') return !!(LOG_CATS[x] && LOG_CATS[x](e));
  if(k === 'msg') return e.kind === 'msg'
    && (x === 'owner' ? e.role === 'owner' : e.role !== 'owner');
  if(k === 'click') return e.type === 'ui.click'
    && (x === 'operator' ? e.actor === 'operator' : e.actor !== 'operator');
  if(k === 'type') return e.kind === 'event' && e.type === x;
  return true;
}
$('#lf-kind').onchange = e => {
  logFilter.kind = e.target.value; logSig = ''; loadActivity();
};
let activityMode = 'term';
$('#activity-mode').onclick = () => {
  activityMode = activityMode === 'term' ? 'table' : 'term';
  $('#activity-mode').textContent = activityMode === 'term' ? 'Structured log' : 'Terminal view';
  logSig = ''; loadActivity();
};
async function loadActivity(){
  let events = [];
  try{ events = await (await fetch('/api/activity')).json(); }
  catch(e){ $('#activity-meta').textContent = 'disconnected'; return; }
  $('#activity-meta').textContent = 'live · refreshes every 3s';
  events = events.filter(e =>
    (!logFilter.proj || String(e.project_num ?? '').startsWith(logFilter.proj))
    && (!logFilter.client
        || String(e.client_id ?? '').startsWith(logFilter.client))
    && (!logFilter.bot || e.kind === 'msg')
    && matchKind(e));
  const sig = (events.length
    ? events[0].kind + events[0].seq + ':' + events.length : '0')
    + JSON.stringify(logFilter) + activityMode;
  if(sig === logSig) return;   // nothing new — don't disturb the scroll
  logSig = sig;
  const body = $('#activity-body');
  if(activityMode === 'table'){ renderActivityTable(events); return; }
  // terminal semantics: oldest at the top, newest at the prompt line; stick
  // to the bottom unless the user has scrolled up to read history
  const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 40
    || !body.dataset.filled;
  const lines = events.slice().reverse()
    .filter(e => e.type !== 'message.received')
    .map(e => {
    const d = new Date(e.ts*1000);
    const ts = `<span class="ts">[${d.toLocaleDateString('en-CA')} ${
      d.toLocaleTimeString('en-GB')}]</span>`;
    const proj = e.project_id === '__system__' ? 'system'
      : !e.project_id ? 'console' : `PROJECT${e.project_num ?? '?'}`;
    if(e.kind === 'msg'){
      // conversation messages, ops-log style with a direction arrow:
      //   [ts] PROJECT13 Client1 → RequirementBot   (the client writing)
      //   [ts] PROJECT13 RequirementBot → Client1   (the bot answering)
      const client = `<span class="who client">${
        e.client_id != null ? `Client${e.client_id}` : 'owner'}</span>`;
      const P = `<span class="prj">PROJECT${e.project_num ?? '?'}</span>`;
      const B = `<span class="who bot">RequirementBot</span>`;
      const arrow = `<span class="dt">→</span>`;
      const path = e.role === 'owner'
        ? `${P} ${client} ${arrow} ${B}` : `${P} ${B} ${arrow} ${client}`;
      const text = e.text.length > 300 ? e.text.slice(0, 300) + '…' : e.text;
      return `<div class="ln">${ts} ${path}  <span class="mt" title="${
        esc(e.text.slice(0, 1000))}">${esc(text)}</span></div>`;
    }
    // 'client'/'owner' role actors resolve to the real Client ID when known
    const actor = (e.actor === 'client' || e.actor === 'owner')
      && e.client_id != null ? `Client${e.client_id}` : e.actor;
    if(e.type === 'project.created'){
      // keep WHO created it; drop the auto-generated name (just a timestamp)
      return `<div class="ln">${ts} <span class="prj">${esc(proj)}</span> ` +
        `<span class="ev">project.created</span> <span class="ac">(${
        esc(actor)})</span></div>`;
    }
    if(e.type === 'ui.click'){
      // clicks read as an action; operator clicks carry no location prefix
      const label = (e.payload.label || '?').replace(/^[>_◎⇶◉≡\s]+/, '');
      const loc = proj === 'console' ? ''
        : `<span class="prj">${esc(proj)}</span> `;
      return `<div class="ln dim">${ts} ${loc}` +
        `<span class="ac">${esc(actor)}</span> <span class="dt">→ clicked</span> ` +
        `<span class="mt">"${esc(label)}"</span>${e.payload.view
          ? ` <span class="dt">in ${esc(String(e.payload.view).replace('view-',''))}</span>` : ''}</div>`;
    }
    const detail = Object.entries(e.payload||{})
      .map(([k,v]) => `${k}=${typeof v==='object'?JSON.stringify(v):v}`).join(' ');
    const cls = /fail|error/.test(e.type) ? ' err'
      : /review.requested|reset/.test(e.type) ? ' warn' : '';
    const dim = e.type === 'revision.committed' ? ' dim' : '';
    return `<div class="ln${dim}">${ts} <span class="prj">${
      esc(proj)}</span> <span class="ev${cls}">${esc(e.type)}</span> <span class="ac">(${
      esc(actor)})</span>${detail ? ` <span class="dt">${esc(detail)}</span>` : ''}</div>`;
  }).join('');
  body.innerHTML = lines
    ? lines + `<div class="ln"><span class="pr">$</span> <span class="cursor"></span></div>`
    : `<div class="empty-state"><b>No activity yet</b><span>Live events,
       conversations, and interactions will stream here as they happen.</span></div>`;
  body.dataset.filled = '1';
  if(stick) body.scrollTop = body.scrollHeight;
}

/* ================= Workflows (monitoring, read-only) ================= */
const pipelinesUI = {data:null, expanded:new Set(), tab:{}, detail:{}};

const STAGE_ICONS = {interviewing:'💬', architecture:'📐', building:'🔨',
  testing_repair:'🧪', bot_created:'🏁'};
const FLOW_STATE = {interviewing:'Interviewing', architecture:'Architecting',
  building:'Building', testing_repair:'Testing & Repairing',
  bot_created:'Completed'};
function fmtTok(n){
  if(n==null) return '—';
  if(n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if(n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return String(n);
}
function fmtSecs(s){
  if(s==null) return 'time —';
  s = Math.round(s);
  if(s >= 3600) return `${Math.floor(s/3600)}h ${Math.floor(s%3600/60)}m`;
  return s >= 60 ? `${Math.floor(s/60)}m ${s%60}s` : `${s}s`;
}
function ago(ts){
  if(!ts) return 'Unknown';
  const s = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if(s < 60) return s + 's ago';
  if(s < 3600) return Math.floor(s/60) + 'm ago';
  if(s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

async function loadPipelines(){
  try{ pipelinesUI.data = await (await fetch('/api/pipelines')).json(); }
  catch(e){ return; }
  renderFlows();
  // refresh any open detail panels from records (read-only, no side effects)
  pipelinesUI.expanded.forEach(pid => loadFlowDetail(pid));
}

function flowMatches(f, q, filt){
  if(filt === 'active' && f.current_stage !== 'interviewing') return false;
  if(filt === 'review' && !(f.open_reviews > 0
      || (f.current_stage === 'architecture' && !f.approved))) return false;
  if(filt === 'approved' && !f.approved) return false;
  if(filt === 'test' && !f.is_test) return false;
  if(q){
    const hay = (f.name + ' ' + (f.contact||'') + ' ' + f.flow_id).toLowerCase();
    if(!hay.includes(q)) return false;
  }
  return true;
}

function renderFlows(){
  if(!pipelinesUI.data) return;
  const q = '', filt = 'all';
  const tpl = pipelinesUI.data.template.stages;
  const flows = pipelinesUI.data.flows.filter(f => flowMatches(f, q, filt));
  const list = $('#pipelines-list');
  if(!flows.length){
    list.innerHTML = `<div class="empty-state">${pipelinesUI.data.flows.length
      ? '<b>No matching workflows</b><span>No workflows match the current filter.</span>'
      : '<b>No workflows yet</b><span>New client conversations appear here automatically. A workflow can also be started with New Session on the Home page.</span>'}</div>`;
    return;
  }
  list.innerHTML = flows.map(f => {
    const steps = tpl.map((t, i) => {
      const s = f.stages.find(x => x.id === t.id) || {status:'not_started'};
      const busyCls = (s.status==='current' && f.busy) ? ' busy' : '';
      const badge = s.status==='completed' ? '✓' : (i+1);
      const showWho = s.status==='current' || s.status==='blocked'
        || t.id==='interviewing';
      const m = (f.stage_metrics||{})[t.id];
      // state-aware statuses: the Interview step says what is actually
      // happening (the bot computing vs. waiting on the person); the
      // architecture gate reads Under Review; other current steps Running.
      const simple = s.status==='completed' ? 'Completed'
        : (s.status==='current' || s.status==='blocked')
          ? (t.id==='interviewing'
              ? (f.busy ? 'Preparing Message' : 'Waiting for Response')
              : t.id==='architecture' ? 'Under Review' : 'Running')
          : 'Pending';
      const stSpin = (s.status==='current' || s.status==='blocked')
        && (t.id!=='interviewing' || f.busy)
        ? '<span class="spin"></span>' : '';
      return `<div class="fc-step ${s.status}${busyCls}">
        <span class="cn" aria-hidden="true"><span class="ico">${STAGE_ICONS[t.id]||'•'}</span>
          <span class="numb">${badge}</span></span>
        <div><span class="lbl">${esc(t.label)}</span>
        ${showWho ? `<span class="who">${esc(t.who)}</span>` : ''}
        ${t.id==='interviewing' && m && m.calls && m.model
          ? `<span class="mmodel" title="Model running this stage">${esc(m.model)}</span>` : ''}
        <span class="st" title="${esc(s.note||'')}">${stSpin}${simple}</span>
        ${t.id!=='interviewing' && m && m.calls ? `<span class="mrow">
          <span class="mcol" title="Cost of this stage so far">
            <em>Total Price</em><b>${m.cost_usd!=null
              ? '$'+m.cost_usd.toFixed(2) : '—'}</b></span>
          <span class="mcol" title="Active model processing time (waiting excluded)">
            <em>Total Time</em><b>${fmtSecs(m.active_seconds)}</b></span>
          <span class="mcol" title="Model running this stage">
            <em>Model</em><b>${esc(m.model||'—')}</b></span>
        </span>` : ''}

        ${t.id==='interviewing' && m && m.calls ? `<span class="mrow mstack">
          <span class="mcard">
            <b class="mpill mtotal" title="Total cost of this stage so far">${
              m.cost_usd!=null ? '$'+m.cost_usd.toFixed(2) : '—'}</b>
            <span class="mpair">
              <span class="mcol" title="Input side (conversation fed into the model)">
                <em>Human Cost</em><b>${m.cost_in_usd!=null
                  ? '$'+m.cost_in_usd.toFixed(2) : '—'}</b></span>
              <span class="mcol" title="Output side (what the bot generated)">
                <em>Bot Cost</em><b>${m.cost_out_usd!=null
                  ? '$'+m.cost_out_usd.toFixed(2) : '—'}</b></span>
            </span>
            <span class="mpair">
              <span class="mcol"><em>Input Tokens</em><b>${fmtTok(m.tokens_in)}</b></span>
              <span class="mcol"><em>Output Tokens</em><b>${fmtTok(m.tokens_out)}</b></span>
            </span>
            <span class="mpair">
              <span class="mcol" title="Tokens served from the prompt cache at 10% price">
                <em>Cache Read</em><b>${fmtTok(m.cache_read)}</b></span>
              <span class="mcol" title="Tokens written into the prompt cache">
                <em>Cache Write</em><b>${fmtTok(m.cache_write)}</b></span>
            </span>
            <span class="mpair">
              <span class="mcol" title="Messages received from the client">
                <em>Received</em><b class="mpill">${m.msgs_received ?? '—'}</b></span>
              <span class="mcol" title="Messages the bot sent">
                <em>Sent</em><b class="mpill">${m.msgs_sent ?? '—'}</b></span>
            </span>
            <span class="mpair mtime">
              <span class="mcol" title="Conversation duration: first message to the last (to now while the interview is still open)">
                <em>Total</em><b>${fmtSecs(m.elapsed_seconds ?? m.active_seconds)}</b></span>
              <span class="mcol" title="Average client reply gap">
                <em>Avg Receive</em><b>${m.avg_client_seconds!=null
                  ? fmtSecs(m.avg_client_seconds) : '—'}</b></span>
              <span class="mcol" title="Average bot reply time">
                <em>Avg Send</em><b>${m.avg_bot_seconds!=null
                  ? fmtSecs(m.avg_bot_seconds) : '—'}</b></span>
            </span>
            <span class="mpair">
              <span class="mjson"><a href="#" data-viewer="input"
                data-pid="${f.flow_id}">View Input</a></span>
              ${f.head_rev ? `<span class="mjson"><a href="#" data-viewer="output"
                data-pid="${f.flow_id}" data-rev="${f.head_rev}">View Output</a></span>` : ''}
            </span>
          </span>
        </span>` : ''}
        </div></div>`;
    }).join('');
    const cur = tpl.find(t => t.id === f.current_stage);
    const curStage = f.stages.find(x => x.id === f.current_stage) || {};
    return `<div class="flow-card${pipelinesUI.expanded.has(f.flow_id)?' open':''}" data-fid="${f.flow_id}">
      <div class="fc-head" role="button" tabindex="0"
        aria-expanded="${pipelinesUI.expanded.has(f.flow_id)}"
        aria-label="Flow ${esc(f.name)} — expand details">
        <div class="fc-left">
          <span class="fc-flowstate">${f.finished
            ? `<span class="fs-done">✓</span> Completed`
            : `<span class="spin"></span>${esc(FLOW_STATE[f.current_stage]||'—')}`}</span>
          <span class="fc-idbig" title="${esc(f.project_name || '')} · ${esc(f.flow_id)}">Project ${f.num ?? '—'}
            ${f.is_test ? `<span class="fc-test">Test</span>` : ''}</span>
          ${f.client_id != null
            ? `<span class="fc-client">Client ${f.client_id}</span>` : ''}
          <span class="fc-ts">${f.created_ts
            ? new Date(f.created_ts*1000).toLocaleString([], {month:'short',
                day:'numeric', hour:'2-digit', minute:'2-digit',
                second:'2-digit'}) : '—'}</span>
        </div>
        <span class="fc-cost"><em>${f.finished ? 'Total Cost' : 'Running cost'}</em>
          <b>${f.total_cost_usd != null ? '$'+f.total_cost_usd.toFixed(2) : '—'}</b></span>
      </div>
      <div class="fc-path">${steps}</div>
      <div class="fc-detail">
        <div class="fd-body" id="fd-${f.flow_id}" style="border-radius:6px;
          margin-top:12px">Loading…</div>
      </div>
    </div>`;
  }).join('');

  list.querySelectorAll('.fc-head').forEach(h => {
    const card = h.parentElement, fid = card.dataset.fid;
    const toggle = () => {
      if(pipelinesUI.expanded.has(fid)){ pipelinesUI.expanded.delete(fid); card.classList.remove('open'); }
      else{ pipelinesUI.expanded.add(fid); card.classList.add('open'); loadFlowDetail(fid); }
      h.setAttribute('aria-expanded', card.classList.contains('open'));
    };
    h.onclick = toggle;
    h.onkeydown = e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(); } };
  });
  pipelinesUI.expanded.forEach(fid => renderFlowDetail(fid));
  list.querySelectorAll('[data-viewer]').forEach(a => a.onclick = e => {
    e.preventDefault(); e.stopPropagation();
    openViewer(a.dataset.viewer, a.dataset.pid, a.dataset.rev);
  });
}

/* ---------- centered content viewer ---------- */
async function openViewer(kind, pid, rev){
  const isIn = kind === 'input';
  $('#viewer-title').textContent = isIn
    ? `Input — conversation · ${pid}`
    : `Output — requirements r${rev} · ${pid}`;
  const dl = $('#viewer-dl');
  dl.innerHTML = isIn
    ? `<a class="act" href="/api/transcript?project=${pid}">Download TXT</a>`
    : `<a class="act" href="/api/export?project=${pid}&format=legacy">Download JSON</a>
       <a class="act" href="/api/export?project=${pid}&format=legacy&as=txt">Download TXT</a>`;
  $('#viewer-pre').textContent = 'Loading…';
  $('#viewer').classList.add('open');
  try{
    const url = isIn ? `/api/transcript?project=${pid}`
                     : `/api/export?project=${pid}&format=legacy`;
    $('#viewer-pre').textContent = await (await fetch(url)).text();
  }catch(e){ $('#viewer-pre').textContent = 'Could not load the content.'; }
}
$('#viewer-close').onclick = () => $('#viewer').classList.remove('open');
$('#viewer').addEventListener('click', e => {
  if(e.target === $('#viewer')) $('#viewer').classList.remove('open');
});

async function loadFlowDetail(fid){
  try{
    pipelinesUI.detail[fid] = await (await fetch('/api/pipeline?project='+fid)).json();
    renderFlowDetail(fid);
  }catch(e){}
}

function renderFlowDetail(fid){
  // Activity only: the recorded events and attempts for this flow.
  const el = document.getElementById('fd-' + fid);
  const d = pipelinesUI.detail[fid];
  if(!el) return;
  if(!d){ el.textContent = 'Loading…'; return; }
  el.innerHTML = (d.events && d.events.length)
    ? `<table><tr><th>When</th><th>Event</th><th>Actor</th></tr>` +
      d.events.map(e => `<tr><td>${new Date(e.ts*1000).toLocaleString()}</td>
        <td>${esc(e.type)}</td><td>${esc(e.actor)}</td></tr>`).join('') + `</table>`
    : '<span style="color:var(--muted)">No recorded events.</span>';
}

/* ================= sessions (owner testing) & review ================= */
const proj = {id:'default'};
const chat = {loaded:false, busy:false, complete:false};

async function loadProjects(){
  let list = [];
  try{ list = await (await fetch('/api/projects')).json(); }catch(e){ return; }
  for(const sel of ['#proj-select-chat']){
    $(sel).innerHTML = list.map(p =>
      `<option value="${p.id}"${p.id===proj.id?' selected':''}>${esc(p.name)} (${p.state})</option>`).join('');
  }
}
function switchProject(id){
  proj.id = id; chat.loaded = false;
  loadProjects();
  if(document.body.className === 'view-chat') loadChat();
}
$('#proj-select-chat').onchange = e => switchProject(e.target.value);
$('#proj-new').onclick = async () => {
  const name = prompt('New TEST session name (real clients get their own via the client link):');
  if(!name) return;
  const d = await (await fetch('/api/projects', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name})})).json();
  if(d.id) switchProject(d.id);
};

function renderChat(d){
  chat.complete = d.complete;
  const t = $('#chat-thread');
  t.innerHTML = d.messages.map(m =>
    `<div class="msg ${m.role === 'human' ? 'human' : 'ai'}">
       <div class="who">${m.role === 'human' ? 'You' : 'Requirements Bot'}</div>${esc(m.text)}</div>`
  ).join('')
  + (d.error ? `<div class="msg err">${esc(d.error)}</div>` : '')
  + (d.saved ? `<div class="msg sys">✓ Interview complete — full brief saved to ${d.saved}</div>` : '')
  + (chat.busy ? `<div id="chat-typing"><span class="spin"></span>Requirements Bot is thinking…</div>` : '');
  t.scrollTop = t.scrollHeight;
  $('#chat-send').disabled = chat.busy || chat.complete;
  $('#chat-input').disabled = chat.complete;
}
async function loadChat(){
  chat.loaded = true;
  loadProjects();
  try{ renderChat(await (await fetch('/chat/history?project='+proj.id)).json()); }
  catch(e){ chat.loaded = false; }
}
async function sendChat(){
  const inp = $('#chat-input'), text = inp.value.trim();
  if(!text || chat.busy || chat.complete) return;
  chat.busy = true; inp.value = '';
  const t = $('#chat-thread');
  t.insertAdjacentHTML('beforeend',
    `<div class="msg human"><div class="who">You</div>${esc(text)}</div>
     <div id="chat-typing"><span class="spin"></span>Requirements Bot is thinking…</div>`);
  t.scrollTop = t.scrollHeight;
  $('#chat-send').disabled = true;
  // While the reply is in flight, watch the api_gate: if our call is stuck
  // on the rate limit, say THAT instead of a generic thinking spinner.
  const watcher = setInterval(async () => {
    const el = document.getElementById('chat-typing');
    if(!el) return;
    try{
      const q = await (await fetch('/api/rate_limits')).json();
      el.innerHTML = q.waiting_capacity
        ? '<span class="spin"></span>Waiting for API capacity — background '
          + 'tests are using the rate limit; your reply goes first…'
        : '<span class="spin"></span>Requirements Bot is thinking…';
    }catch(_){}
  }, 2000);
  try{
    const d = await (await fetch('/chat/send', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: text, project: proj.id})})).json();
    chat.busy = false; renderChat(d);
  }catch(e){
    chat.busy = false;
    document.getElementById('chat-typing')?.remove();
    t.insertAdjacentHTML('beforeend',
      `<div class="msg err">Request failed — is the server still running?</div>`);
    $('#chat-send').disabled = false;
  }
  clearInterval(watcher);
  inp.focus();
}
async function uploadChatFiles(fileList){
  const files = await Promise.all([...fileList].map(f => new Promise(res => {
    const r = new FileReader();
    r.onload = () => res({name: f.name, data_b64: r.result.split(',')[1]});
    r.readAsDataURL(f);
  })));
  if(!files.length) return;
  const t = $('#chat-thread');
  try{
    const d = await (await fetch('/chat/upload', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({files, project: proj.id})})).json();
    if(d.saved && d.saved.length)
      t.insertAdjacentHTML('beforeend',
        `<div class="msg sys">📎 Uploaded: ${esc(d.saved.join(', '))} — now tell the bot the files are ready.</div>`);
    (d.rejected||[]).forEach(msg =>
      t.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(msg)}</div>`));
  }catch(e){
    t.insertAdjacentHTML('beforeend', `<div class="msg err">Upload failed.</div>`);
  }
  t.scrollTop = t.scrollHeight;
  $('#chat-file').value = '';
}
$('#chat-attach').onclick = () => $('#chat-file').click();
$('#chat-file').onchange = e => uploadChatFiles(e.target.files);
$('#chat-thread').addEventListener('dragover', e => {
  e.preventDefault(); e.currentTarget.classList.add('drop');
});
$('#chat-thread').addEventListener('dragleave', e =>
  e.currentTarget.classList.remove('drop'));
$('#chat-thread').addEventListener('drop', e => {
  e.preventDefault(); e.currentTarget.classList.remove('drop');
  uploadChatFiles(e.dataTransfer.files);
});
$('#chat-send').onclick = sendChat;
$('#chat-input').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); }
});
$('#chat-reset').onclick = async () => {
  if(chat.busy) return;
  const d = await (await fetch('/chat/reset', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({project: proj.id})})).json();
  chat.complete = false; renderChat(d); $('#chat-input').disabled = false;
};

/* ---------- UI click telemetry: every console click lands in the log ---- */
document.addEventListener('click', e => {
  const t = e.target.closest(
    'button, a, .nav-item, select, .fc-head, .gnode, .gbtn, input');
  if(!t) return;
  const label = (t.getAttribute('aria-label') || t.title
    || t.textContent || t.placeholder || t.tagName).trim().slice(0, 80);
  try{ fetch('/api/ui-event', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({label, view: document.body.className})}); }catch(_){}
}, true);

loadSystem();
setInterval(() => {
  if(document.body.className === 'view-overview') loadSystem();
  if(document.body.className === 'view-pipelines') loadPipelines();
}, 5000);
setInterval(() => {
  if(document.body.className === 'view-activity') loadActivity();
}, 3000);
setInterval(() => {
  if(document.body.className === 'view-customers') loadCustomers();
}, 5000);
setInterval(() => {
  if(['view-experiments', 'view-testsuites'].includes(document.body.className))
    loadSim();
}, 3000);
</script></body></html>"""


# ============================ CLIENT CHAT PAGE ============================
CLIENT_PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BotBot — Let's plan your chatbot</title>
<style>
:root{
  --bg:#0d0d0d; --panel:#151515; --panel2:#1a1a1a; --border:#2a2a2a;
  --border-hi:#3f3f46; --text:#f5f5f5; --text2:#a1a1aa; --muted:#71717a;
  --accent:#6ea8fe; --green:#4ade80; --red:#f87171;
  --mono:'Cascadia Code',Consolas,'SF Mono',monospace;
  --sans:-apple-system,'Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);
  display:flex;justify-content:center;min-height:100vh}
#wrap{width:100%;max-width:760px;display:flex;flex-direction:column;height:100vh}
header{padding:16px 20px 12px;border-bottom:1px solid var(--border)}
header .brand{display:flex;align-items:center;gap:10px}
header .logo{width:26px;height:26px;border:1px solid var(--border-hi);border-radius:6px;
  display:grid;place-items:center;font:600 12px var(--mono);color:var(--accent)}
header h1{margin:0;font-size:16px;font-weight:600}
header p{margin:6px 0 0;color:var(--text2);font-size:12.5px}
#thread{flex:1;overflow-y:auto;padding:18px 20px;display:flex;
  flex-direction:column;gap:12px}
.msg{max-width:82%;border:1px solid var(--border);border-radius:10px;
  padding:9px 13px;white-space:pre-wrap;overflow-wrap:break-word}
.msg .who{font:600 9.5px var(--mono);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);margin-bottom:3px}
.msg.ai{align-self:flex-start;background:var(--panel2)}
.msg.human{align-self:flex-end;background:#161a20;border-color:#2b3a52}
.msg.err{align-self:stretch;max-width:none;border-color:#553030;color:var(--red);
  font-size:12.5px}
.msg.sys{align-self:center;max-width:none;border:none;background:none;
  color:var(--green);font:12px var(--mono);text-align:center}
#typing{align-self:flex-start;color:var(--muted);font:12px var(--mono);padding:2px 4px}
.spin{display:inline-block;width:10px;height:10px;border:1.5px solid var(--border-hi);
  border-top-color:var(--accent);border-radius:50%;animation:rot .8s linear infinite;
  margin-right:6px;vertical-align:-1px}
@keyframes rot{to{transform:rotate(360deg)}}
#bar{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--border);
  align-items:flex-end}
#inp{flex:1;background:var(--panel2);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:10px 13px;font:14px/1.5 var(--sans);resize:none;
  min-height:42px;max-height:150px}
#inp:focus{outline:none;border-color:var(--border-hi)}
#send{background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);border-radius:8px;
  padding:10px 18px;font:600 13px var(--sans);cursor:pointer}
#send:hover{border-color:var(--accent)}
#send:disabled{opacity:.5;cursor:default}
#attach{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:8px;padding:10px 12px;font-size:14px;cursor:pointer}
#attach:hover{border-color:var(--border-hi);color:var(--text)}
#thread.drop{outline:1px dashed var(--accent);outline-offset:-6px}
footer{padding:6px 16px 12px;text-align:center;font:10.5px var(--mono);color:var(--muted)}
</style></head><body>
<div id="wrap">
  <header>
    <div class="brand"><div class="logo">B</div><h1>Let's plan your chatbot</h1></div>
    <p>Answer a few questions about your business. You can also attach real
    customer conversations (chat exports or screenshots) when asked — they help
    us ask better questions.</p>
  </header>
  <div id="thread" aria-live="polite"></div>
  <div id="bar">
    <input type="file" id="file" multiple hidden
           accept=".txt,.md,.csv,.png,.jpg,.jpeg,.webp,.gif">
    <button id="attach" title="Attach chat exports / screenshots">📎</button>
    <textarea id="inp" rows="1" placeholder="Type your answer…" spellcheck="false"></textarea>
    <button id="send">Send</button>
  </div>
  <footer>Your answers are saved as you go. Refreshing this page continues the
  same conversation.</footer>
</div>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
const st = {busy:false, complete:false, started:false};

function render(d){
  st.complete = d.complete; st.started = d.started;
  $('#thread').innerHTML = d.messages.map(m =>
    `<div class="msg ${m.role==='human'?'human':'ai'}">
       <div class="who">${m.role==='human'?'You':'BotBot'}</div>${esc(m.text)}</div>`).join('')
  + (d.error ? `<div class="msg err">${esc(d.error)}</div>` : '')
  + (d.complete ? `<div class="msg sys">✓ All done — thank you! We have everything
      we need for now, and our team will review your answers and follow up.</div>` : '')
  + (st.busy ? `<div id="typing"><span class="spin"></span>thinking…</div>` : '');
  $('#thread').scrollTop = $('#thread').scrollHeight;
  $('#send').disabled = st.busy || st.complete;
  $('#inp').disabled = st.complete;
}
async function load(){
  try{ render(await (await fetch('/client/history')).json()); }
  catch(e){ render({messages:[], error:'Could not reach the server. Please refresh.'}); }
}
async function send(){
  const text = $('#inp').value.trim();
  if(!text || st.busy || st.complete) return;
  st.busy = true; $('#inp').value = '';
  $('#thread').insertAdjacentHTML('beforeend',
    `<div class="msg human"><div class="who">You</div>${esc(text)}</div>
     <div id="typing"><span class="spin"></span>thinking…</div>`);
  $('#thread').scrollTop = $('#thread').scrollHeight;
  $('#send').disabled = true;
  // Honest spinner: if our reply is queued on API capacity, say so.
  const watcher = setInterval(async () => {
    const el = document.getElementById('typing');
    if(!el) return;
    try{
      const q = await (await fetch('/client/queue')).json();
      el.innerHTML = q.waiting_capacity
        ? '<span class="spin"></span>Waiting for API capacity — your reply is next in line…'
        : '<span class="spin"></span>thinking…';
    }catch(_){}
  }, 2000);
  try{
    const d = await (await fetch('/client/send', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: text})})).json();
    st.busy = false; render(d);
  }catch(e){
    st.busy = false;
    document.getElementById('typing')?.remove();
    $('#thread').insertAdjacentHTML('beforeend',
      `<div class="msg err">Something went wrong sending that. Your earlier
       answers are saved — please try again.</div>`);
    $('#send').disabled = false;
  }
  clearInterval(watcher);
  $('#inp').focus();
}
async function upload(fileList){
  if(!st.started){
    $('#thread').insertAdjacentHTML('beforeend',
      `<div class="msg err">Please send a message first, then attach your files.</div>`);
    return;
  }
  const files = await Promise.all([...fileList].map(f => new Promise(res => {
    const r = new FileReader();
    r.onload = () => res({name: f.name, data_b64: r.result.split(',')[1]});
    r.readAsDataURL(f);
  })));
  if(!files.length) return;
  try{
    const d = await (await fetch('/client/upload', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({files})})).json();
    if(d.saved && d.saved.length)
      $('#thread').insertAdjacentHTML('beforeend',
        `<div class="msg sys">📎 Received: ${esc(d.saved.join(', '))} — now tell
         BotBot the files are ready.</div>`);
    (d.rejected||[]).forEach(m =>
      $('#thread').insertAdjacentHTML('beforeend', `<div class="msg err">${esc(m)}</div>`));
  }catch(e){
    $('#thread').insertAdjacentHTML('beforeend',
      `<div class="msg err">Upload failed — please try again.</div>`);
  }
  $('#thread').scrollTop = $('#thread').scrollHeight;
  $('#file').value = '';
}
document.addEventListener('click', e => {
  const t = e.target.closest('button, a, input, textarea');
  if(!t) return;
  const label = (t.getAttribute('aria-label') || t.title || t.textContent
    || t.placeholder || t.tagName).trim().slice(0, 80);
  try{ fetch('/client/ui-event', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({label})}); }catch(_){}
}, true);
$('#send').onclick = send;
$('#inp').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); }
});
$('#attach').onclick = () => $('#file').click();
$('#file').onchange = e => upload(e.target.files);
$('#thread').addEventListener('dragover', e => {
  e.preventDefault(); e.currentTarget.classList.add('drop'); });
$('#thread').addEventListener('dragleave', e =>
  e.currentTarget.classList.remove('drop'));
$('#thread').addEventListener('drop', e => {
  e.preventDefault(); e.currentTarget.classList.remove('drop');
  upload(e.dataTransfer.files); });
load();
</script></body></html>"""


# ============================ BACKEND ============================
def _project_of(value) -> str:
    pid = str((value.get("project") if isinstance(value, dict) else value) or "").strip()
    store.ensure_default_project()
    if not pid or store.get_project(pid) is None:
        return store.DEFAULT_PROJECT
    return pid


def chat_history(pid: str) -> dict:
    return controller.chat_payload(pid)


def chat_send(pid: str, message: str) -> dict:
    if not message:
        return chat_history(pid) | {"error": "empty message"}
    out = controller.send_interview_message(pid, message)
    if "messages" not in out:
        out = chat_history(pid) | out
    return out


_api_status_cache = {"ts": 0.0, "status": "checking", "detail": "",
                     "limits": {}}

_RL_MODELS = ("claude-sonnet-5", "claude-haiku-4-5")


def _parse_ratelimit_headers(headers) -> dict:
    out = {}
    for metric in ("requests", "input-tokens", "output-tokens"):
        limit = headers.get(f"anthropic-ratelimit-{metric}-limit")
        remaining = headers.get(f"anthropic-ratelimit-{metric}-remaining")
        if limit is not None and remaining is not None:
            out[metric] = {"limit": int(limit), "remaining": int(remaining),
                           "reset": headers.get(
                               f"anthropic-ratelimit-{metric}-reset")}
    return out


def _api_status() -> dict:
    """Live Claude API connection status, probed with the FREE token-count
    endpoint (no tokens are billed) at most once a minute. Backward-looking
    'last call ok' can hide an expired key for hours; this says whether the
    connection works RIGHT NOW."""
    now = time.time()
    if now - _api_status_cache["ts"] < 60:
        return dict(_api_status_cache)
    status, detail = "connected", ""
    try:
        import anthropic
        from dotenv import load_dotenv
        load_dotenv(str(Path(__file__).parent / ".env"))
        if not __import__("os").environ.get("ANTHROPIC_API_KEY"):
            status, detail = "no API key", "ANTHROPIC_API_KEY is not set"
        else:
            # The probe doubles as the rate-limit sampler: a 1-output-token
            # message per model (fractions of a cent per day) whose response
            # HEADERS carry the live per-model RPM/ITPM/OTPM meters. The
            # free token-count endpoint cannot serve here - it does not
            # return the token-bucket headers.
            client = anthropic.Anthropic()
            limits = {}
            for model in _RL_MODELS:
                raw = client.with_options(
                    timeout=8.0, max_retries=0).messages.with_raw_response.create(
                    model=model, max_tokens=1,
                    messages=[{"role": "user", "content": "."}])
                limits[model] = _parse_ratelimit_headers(raw.headers)
            _api_status_cache["limits"] = limits
    except Exception as e:
        import anthropic
        if isinstance(e, (anthropic.AuthenticationError,
                          anthropic.PermissionDeniedError)):
            status = "auth error"
        elif isinstance(e, anthropic.RateLimitError):
            status = "rate limited"
        elif isinstance(e, anthropic.APIStatusError) and e.status_code >= 500:
            status = "Anthropic overloaded"
        elif isinstance(e, (anthropic.APITimeoutError,
                            anthropic.APIConnectionError)):
            status = "unreachable"
        elif "credit balance" in str(e).lower():
            status = "out of credits"
        else:
            status = "error"
        detail = f"{type(e).__name__}"
    _api_status_cache.update(ts=now, status=status, detail=detail)
    return dict(_api_status_cache)


def system_payload() -> dict:
    store.ensure_default_project()
    return {
        "stats": store.system_stats(),
        "active_runs": controller.active_runs(),
        "capabilities": registry.capabilities(),
        "health": store.health() | {
            "last_call": store.last_provider_event(),
            "supervisor_mode": supervisor.mode(),
            "api": _api_status()},
        "client_link": {"path": "/chat", "local_only": True},
    }


def pipelines_payload() -> dict:
    """Read-model for the Workflows monitor: one deterministic projection
    per journey, driven by recorded state only. Reading it never calls a
    model, creates an interview, or advances anything."""
    store.ensure_default_project()
    client_ids = store.client_project_ids()
    planned = {c["id"] for c in registry.capabilities() if c.get("planned")}
    rows = [r for r in store.flows_summary()
            # the auto-created default placeholder is not a journey until
            # someone has actually used it
            if not (r["id"] == store.DEFAULT_PROJECT
                    and not r["has_session"] and not r["head_rev"])]
    flows = []
    for r in rows:
        f = controller.flow_projection(r, planned, r["id"] in client_ids)
        # real spend of the stages that actually run today
        metrics = {"interviewing": store.interviewing_metrics(r["id"])}
        f["stage_metrics"] = metrics
        # flow-level cost: sum of known stage costs; a stage that ran on an
        # unpriced model makes the total unknown rather than understated
        costs = [m.get("cost_usd") for m in metrics.values() if m.get("calls")]
        f["total_cost_usd"] = (None if any(c is None for c in costs)
                               else round(sum(costs), 4) if costs else 0.0)
        f["finished"] = any(s["id"] == "bot_created" and s["status"] == "completed"
                            for s in f["stages"])
        flows.append(f)
    return {"template": controller.FLOW_TEMPLATE, "flows": flows}


def pipeline_detail(pid: str) -> dict:
    """Read-only inspection of one flow: recorded events, the clean display
    transcript (no composer, no raw JSON), the exact revision/approval state,
    and artifacts that actually exist."""
    rp = review_payload(pid)
    return {
        "flow_id": pid,
        "events": store.recent_events(pid, 30),
        "transcript": controller.chat_payload(pid)["messages"],
        "review": rp,
        "artifacts": ([{"kind": "requirements_brief",
                        "label": f"Requirements brief · rev r{rp['head']}",
                        "available": True, "formats": ["legacy", "extended"]}]
                      if rp["head"] else []),
    }


def review_payload(pid: str) -> dict:
    head = store.get_revision(pid)
    project = store.get_project(pid) or {}
    return {
        "state": project.get("state"),
        "head": head and head["rev"],
        "revisions": store.list_revisions(pid),
        "readiness": controller.evaluate_readiness_readonly(pid),
        "reviews": store.all_reviews(pid)[:30],
        "approval": store.active_approval(pid),
    }


ALLOWED_EXTS = {".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def save_uploads(pid: str, files: list, actor: str = "client") -> dict:
    """Validated upload into the PROJECT's own materials dir."""
    import base64
    updir = controller.uploads_dir(pid)
    updir.mkdir(parents=True, exist_ok=True)
    saved, rejected = [], []
    for f in files[:20]:
        name = Path(str(f.get("name", ""))).name  # strip any path components
        ext = Path(name).suffix.lower()
        if not name or ext not in ALLOWED_EXTS:
            rejected.append(f"{name or '(unnamed)'} — only "
                            + " ".join(sorted(ALLOWED_EXTS)) + " files are readable")
            continue
        try:
            data = base64.b64decode(str(f.get("data_b64", "")), validate=True)
        except Exception:
            rejected.append(f"{name} — could not decode")
            continue
        if len(data) > 15 * 1024 * 1024:
            rejected.append(f"{name} — larger than 15MB")
            continue
        (updir / name).write_bytes(data)
        saved.append(name)
    if saved:
        store.append_event(pid, "materials.uploaded", actor, {"files": saved})
    return {"saved": saved, "rejected": rejected}


# ---------- client-side payloads (no internals, redacted errors) ----------
CLIENT_MAX_MESSAGES = 200


def client_history(pid: str | None) -> dict:
    from requirements_bot import RequirementsBot
    if pid is None:  # no session yet: the static greeting, nothing created
        return {"messages": [{"role": "ai", "text": RequirementsBot.GREETING}],
                "complete": False, "started": False}
    return controller.chat_payload(pid) | {"started": True}


def client_send(pid: str, message: str) -> dict:
    if len(message) > 4000:
        return client_history(pid) | {"error": "That message is too long — "
                                      "please split it up."}
    if store.count_client_messages(pid) >= CLIENT_MAX_MESSAGES:
        return client_history(pid) | {"error": "This conversation has reached "
                                      "its limit. Please contact us directly."}
    out = controller.send_interview_message(pid, message)
    if "error" in out:
        err = str(out["error"])
        # Clients see actionable-but-safe text only; details stay in events.
        if not (err.startswith("Stopped —") or "wait a moment" in err):
            out["error"] = ("Something went wrong on our side. Your answers "
                            "are saved — please try again in a moment.")
    if "messages" not in out:
        out = controller.chat_payload(pid) | {"error": out.get("error")}
    out.pop("saved", None)          # internal filename, not for clients
    out["started"] = True
    return out


class Handler(BaseHTTPRequestHandler):
    # ---------------- plumbing ----------------
    def _send(self, body: bytes, ctype: str, extra: dict | None = None,
              status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200, extra: dict | None = None):
        self._send(json.dumps(obj, default=str).encode("utf-8"),
                   "application/json", extra, status)

    def _cookies(self) -> dict:
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _is_owner(self) -> bool:
        return secrets.compare_digest(
            self._cookies().get("botbot_owner", ""), _owner_token())

    def _client_pid(self) -> str | None:
        """The ONLY authorization a client request gets: its own session
        cookie resolved server-side. Owner cookies and any client-supplied
        project ids are ignored on client routes."""
        return store.resolve_client_session(
            self._cookies().get("botbot_client", ""))

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True     # same-origin non-CORS requests may omit it
        return origin in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")

    # ---------------- GET ----------------
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        route = u.path

        # pages
        if route in ("/", "/admin"):
            # Owner console served on localhost; sets the owner cookie that
            # every owner API call requires. NOT internet-grade auth.
            # no-store: a UI update must show on plain refresh, never a
            # stale cached page.
            self._send(ADMIN_PAGE.encode("utf-8"), "text/html; charset=utf-8",
                       {"Set-Cookie": f"botbot_owner={_owner_token()}; "
                                      f"Path=/; HttpOnly; SameSite=Lax",
                        "Cache-Control": "no-store"})
            return
        if route == "/chat":
            extra = None
            if q.get("new") == "1":
                # Explicit fresh start (the owner's New Session button):
                # revoke the old client session (records preserved), create
                # the new interview NOW so its flow card appears in Chatbot
                # Flows immediately, and bind this browser to it.
                old = self._cookies().get("botbot_client")
                if old:
                    store.revoke_client_session(old)
                pid = store.create_project(
                    "Client " + time.strftime("%Y-%m-%d %H:%M"),
                    actor="client")["id"]
                store.set_project_state(pid, "interviewing")
                token = store.create_client_session(pid)
                extra = {"Set-Cookie": f"botbot_client={token}; Path=/; "
                         f"HttpOnly; SameSite=Lax"}
            self._send(CLIENT_PAGE.encode("utf-8"), "text/html; charset=utf-8",
                       (extra or {}) | {"Cache-Control": "no-store"})
            return

        # client API (client cookie only; never owner, never project params)
        if route == "/client/history":
            self._json(client_history(self._client_pid()))
            return
        if route == "/client/queue":
            # One boolean for the client spinner — no labels, no internals.
            self._json({"waiting_capacity":
                        api_gate.snapshot()["waiting_capacity"]})
            return

        # owner API (owner cookie required — deny by default)
        if not self._is_owner():
            self._json({"error": "owner authorization required"}, status=403)
            return
        pid = _project_of(q.get("project"))
        if route == "/api/system":
            self._json(system_payload())
        elif route == "/api/pipelines":
            self._json(pipelines_payload())
        elif route == "/api/pipeline":
            self._json(pipeline_detail(pid))
        elif route == "/api/projects":
            self._json([{"id": p["id"], "name": p["name"], "state": p["state"]}
                        for p in store.list_projects()])
        elif route == "/api/attention":
            self._json(store.open_reviews_all())
        elif route == "/api/activity":
            self._json(store.recent_terminal_feed())
        elif route == "/api/customers":
            self._json(store.list_clients())
        elif route == "/api/rate_limits":
            self._json(api_gate.snapshot())
        elif route == "/api/billing":
            self._json(store.billing_overview())
        elif route == "/api/sim/personas":
            self._json(store.sim_personas())
        elif route == "/api/sim/runs":
            self._json(store.sim_runs())
        elif route == "/api/sim/feature_catalog":
            from orchestrator import simlab
            self._json(simlab.feature_catalog())
        elif route == "/api/sim/tests":
            self._json(store.sim_tests())
        elif route == "/api/sim/test_report":
            t = store.sim_test(int(q.get("id") or 0))
            self._json({"report": t and t.get("report")})
        elif route == "/api/sim/run_detail":
            self._json(store.sim_run(int(q.get("id") or 0)) or {"error": "unknown run"})
        elif route == "/chat/history":
            self._json(chat_history(pid))
        elif route == "/api/review":
            self._json(review_payload(pid))
        elif route == "/api/management":
            scope = (store.SYSTEM_SCOPE if q.get("scope") == "system" else pid)
            self._json(supervisor.management_payload(scope))
        elif route == "/api/transcript":
            # the interview conversation (the bot's INPUT) as a plain text file
            lines = [f"{'Client' if m['role']=='human' else 'Requirements Bot'}: "
                     f"{m['text']}" for m in controller.chat_payload(pid)["messages"]]
            self._send(("\n\n".join(lines) + "\n").encode("utf-8"),
                       "text/plain; charset=utf-8",
                       {"Content-Disposition":
                        f'attachment; filename="{pid}_conversation.txt"'})
        elif route == "/api/export":
            out = controller.export_package(pid, q.get("format", "legacy"))
            body = json.dumps(out, indent=2, ensure_ascii=False,
                              default=str).encode("utf-8")
            # same JSON content, downloadable as .json or .txt (as=txt)
            as_txt = q.get("as") == "txt"
            self._send(body,
                       "text/plain; charset=utf-8" if as_txt
                       else "application/json",
                       {"Content-Disposition": f'attachment; filename='
                        f'"{pid}_brief.{"txt" if as_txt else "json"}"'})
        else:
            self._json({"error": "unknown endpoint"}, status=404)

    # ---------------- POST ----------------
    def do_POST(self):
        if not self._origin_ok():
            self._json({"error": "bad origin"}, status=403)
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > 32 * 1024 * 1024:
            self._json({"error": "request too large"})
            return
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}

        # ----- client routes: authorized ONLY by the client session cookie
        if self.path == "/client/ui-event":
            pid = self._client_pid()
            if pid is not None:
                store.append_event(pid, "ui.click", "client",
                                   {"label": str(req.get("label", ""))[:80]})
            self._json({"ok": True})
            return
        if self.path == "/client/send":
            pid = self._client_pid()
            extra = None
            if pid is None:
                # First message: create the isolated interview exactly once
                # and issue the session. Merely loading the page never does.
                name = "Client " + time.strftime("%Y-%m-%d %H:%M")
                pid = store.create_project(name, actor="client")["id"]
                token = store.create_client_session(pid)
                extra = {"Set-Cookie": f"botbot_client={token}; Path=/; "
                                       f"HttpOnly; SameSite=Lax"}
            out = client_send(pid, str(req.get("message", "")).strip())
            self._json(out, extra=extra)
            return
        if self.path == "/client/upload":
            pid = self._client_pid()
            if pid is None:
                self._json({"saved": [], "rejected":
                            ["Please send a message first, then attach files."]})
                return
            self._json(save_uploads(pid, req.get("files") or []))
            return

        # ----- owner routes: owner cookie required, deny by default
        if not self._is_owner():
            self._json({"error": "owner authorization required"}, status=403)
            return
        pid = _project_of(req)
        if self.path == "/chat/send":
            out = chat_send(pid, str(req.get("message", "")).strip())
        elif self.path == "/chat/upload":
            out = save_uploads(pid, req.get("files") or [], actor="operator")
        elif self.path == "/chat/reset":
            out = controller.reset_interview(pid)
        elif self.path == "/api/projects":
            name = str(req.get("name", "")).strip()[:80]
            out = store.create_project(name) if name else {"error": "name required"}
        elif self.path == "/api/management":
            scope = (store.SYSTEM_SCOPE if req.get("scope") == "system" else pid)
            out = supervisor.ask(scope, str(req.get("text", "")).strip()[:4000])
        elif self.path == "/api/supervisor/enable":
            out = supervisor.set_enabled(bool(req.get("enabled")))
        elif self.path == "/api/ui-event":
            store.append_event(None, "ui.click", "operator",
                               {"label": str(req.get("label", ""))[:80],
                                "view": str(req.get("view", ""))[:40]})
            out = {"ok": True}
        elif self.path == "/api/sim/personas":
            name = str(req.get("name", "")).strip()[:80]
            kind = req.get("kind") if req.get("kind") in ("ai", "scripted") else "ai"
            content = str(req.get("content", "")).strip()[:8000]
            if not name or not content:
                out = {"error": "name and content required"}
            else:
                out = {"id": store.sim_add_persona(name, kind, content)}
        elif self.path == "/api/sim/run":
            from orchestrator import simlab
            out = simlab.start_run(int(req.get("persona_id") or 0))
        elif self.path == "/api/sim/test":
            from orchestrator import simlab
            kind = str(req.get("kind", ""))
            if kind == "feature_check":
                out = simlab.start_feature_checks(req.get("features") or [])
            else:
                out = simlab.start_test(kind, req)
        elif self.path == "/api/review/resolve":
            ok = store.resolve_review(pid, int(req.get("review_id") or 0),
                                      str(req.get("disposition", ""))[:500]
                                      or "resolved", "operator")
            out = {"ok": ok} if ok else {"error": "review not open"}
        elif self.path == "/api/approve":
            out = controller.approve(pid, int(req.get("revision") or 0),
                                     str(req.get("reason", ""))[:500])
        else:
            out = {"error": "unknown endpoint"}
        self._json(out)

    def log_message(self, *args):  # keep the console quiet
        pass


if __name__ == "__main__":
    print(f"Owner console: http://localhost:{PORT}/admin")
    print(f"Client chat:   http://localhost:{PORT}/chat   (local-only)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
