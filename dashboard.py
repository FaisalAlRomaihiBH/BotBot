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
#chat,#home,#flows{display:none}
body.view-chat #chat{display:flex}
body.view-home #home{display:flex}
body.view-flows #flows{display:flex}
#flows{flex-direction:column;margin:14px 20px 20px;gap:12px;min-height:0}

/* ---------- chatbot flow cards ---------- */
#flows-list{display:flex;flex-direction:column;gap:12px}
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
  justify-content:center}
.fc-step .mcol{display:flex;flex-direction:column;align-items:center;gap:3px;
  padding:0 6px}
.fc-step .mcol em{font:600 8px var(--sans);font-style:normal;
  text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.fc-step .mcol>b{font:500 11px var(--mono);color:var(--text)}
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
.flow-empty{color:var(--muted);text-align:center;padding:40px;font-size:13px;
  background:var(--panel);border:1px solid var(--border);border-radius:8px}
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
#home{flex:1;flex-direction:column;min-height:0;overflow:hidden}
body.view-home #main{overflow:hidden}

/* ---------- Home: header-free, the map IS the page ---------- */
body.view-home #run-header,body.view-flows #run-header{display:none}

/* ---------- orchestrator map: fills the workspace ---------- */
#graph-wrap{flex:1;min-height:0;position:relative;display:flex;
  align-items:center;justify-content:center;background:var(--bg)}
#graph{display:block;width:100%;height:100%}
#home-stale{position:absolute;top:10px;right:16px;font:10px var(--mono);
  color:var(--muted);pointer-events:none}
#home-stale.bad{color:var(--red)}
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
  body.view-home #main{overflow-y:auto}
  #home{overflow:visible}
  #graph-wrap{min-height:420px}
}
</style></head><body class="view-home">
<div id="shell">
  <aside id="sidebar">
    <div id="sb-head"><div id="sb-logo" title="BotBot Orchestrator" aria-label="BotBot Orchestrator">B</div><span id="sb-title">BotBot Orchestrator</span>
      <button id="sb-toggle" title="Collapse">⟨⟩</button></div>
    <nav id="sb-nav">
      <div class="nav-item active" id="nav-home" data-view="home"><span class="nav-ico">◎</span><span class="nav-label">Home</span></div>
      <div class="nav-item" id="nav-flows" data-view="flows"><span class="nav-ico">⇶</span><span class="nav-label">Chatbot Flows</span></div>
    </nav>
    <div id="sb-foot">
      <div><span class="dot" id="dot-store"></span><span id="txt-store">store: checking…</span></div>
      <div><span class="dot" id="dot-provider"></span><span id="txt-provider">model: no calls yet</span></div>
      <div><span class="dot" id="dot-sup"></span><span id="txt-sup">supervisor: —</span></div>
    </div>
  </aside>

  <div id="main">
    <div id="run-header">
      <div><div id="run-title">Operations Console</div></div>
      <div id="run-actions"></div>
    </div>

    <div id="home">
      <div id="graph-wrap" title="Nodes come from the capability registry. Idle means available, not missing. Lines are controller-mediated communication.">
        <svg id="graph" role="img" aria-label="Orchestrator map"></svg>
        <span id="home-stale"></span>
      </div>
    </div>

    <div id="flows">
      <div id="flows-list"></div>
    </div>

    <div id="chat">
      <div id="chat-head">
        <button class="act" id="chat-back" title="Back to Chatbot Flows">←</button>
        <span class="t" style="color:var(--amber)">Owner Test Mode</span>
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
    <textarea id="mgmt-input" rows="1" placeholder="Ask about the platform… (a paid call when enabled)" spellcheck="false"></textarea>
    <button class="act" id="mgmt-send">Ask</button>
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
  if(document.body.className === 'view-home') renderGraph();
};
const VIEW_TITLES = {home:'Operations', flows:'Chatbot Flows',
  chat:'Owner Test Chat'};
function showView(view){
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  document.getElementById('nav-' + (view === 'chat' ? 'flows' : view))
    ?.classList.add('active');
  document.body.className = 'view-' + view;
  $('#run-title').textContent = VIEW_TITLES[view] || 'Operations';
  if(view === 'chat' && !chat.loaded) loadChat();
  if(view === 'home') loadSystem();
  if(view === 'flows') loadFlows();
}
document.querySelectorAll('.nav-item[data-view]').forEach(item =>
  item.onclick = () => showView(item.dataset.view));
$('#chat-back').onclick = () => showView('flows');

/* ================= system-wide Home ================= */
let sys = null;   // last /api/system payload

async function loadSystem(){
  try{ sys = await (await fetch('/api/system')).json(); }
  catch(e){
    $('#home-stale').textContent = 'stale — server unreachable';
    $('#home-stale').className = 'bad';
    return;
  }
  $('#home-stale').textContent = '';
  $('#home-stale').className = '';
  const s = sys;
  const lc = s.health.last_call;

  // honest footer
  $('#dot-store').className = 'dot ' + (s.health.store_ok ? 'ok' : 'bad');
  $('#txt-store').textContent = 'store: ' + (s.health.store_ok ? 'writable' : 'ERROR');
  $('#dot-provider').className = 'dot ' + (lc ? (lc.error ? 'bad' : 'ok') : '');
  $('#txt-provider').textContent = lc
    ? 'model: last ' + lc.purpose + (lc.error ? ' FAILED' : ' ok')
    : 'model: no calls yet';
  const mode = s.health.supervisor_mode;
  $('#dot-sup').className = 'dot ' + (mode==='advisory' ? 'ok' : '');
  $('#txt-sup').textContent = 'supervisor: ' + mode;
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

/* ---------- one geometry pass for the whole map ---------- */
// BotNode: body circle + status ring + centered title/status text.
function graphNode(c, x, y, r, lines, status, dotColor, big, ringCls){
  // Title dead-center of the circle; the status is a small pill badge
  // overlapping the bottom border — half in, half out (chosen style 4).
  const tSize = big ? 25 : 15, sSize = big ? 12 : 10.5, lh = big ? 29 : 19;
  const rows = Array.isArray(status) ? status : [status];
  const titleTop = y - (lines.length * lh)/2;
  let title = '';
  lines.forEach((ln, i) => {
    title += `<text x="${x}" y="${titleTop + lh/2 + i*lh}" text-anchor="middle"
      dominant-baseline="central" fill="var(--text)"
      font-size="${tSize}" font-weight="600">${esc(ln)}</text>`;
  });
  // Status pills are frosted-glass HTML overlays (see .gpill) — SVG rects
  // cannot backdrop-blur. renderGraph collects and lays them.
  const stat = '';
  // The static border is neutral on every node — the center included; the
  // blue belongs to the moving comet ring only.
  const stroke = c.planned ? 'var(--muted)'
    : (big || c.enabled) ? 'var(--border-hi)' : 'var(--border)';
  const dash = (!big && c.planned) ? ' stroke-dasharray="6 5"' : '';
  // Rings are HTML overlays (see .gring) — graphNode draws no ring itself;
  // renderGraph collects {x, y, r, cls} and lays the overlays afterwards.
  const ring = '';
  return `<g class="gnode${c.planned?' planned':''}" data-cap="${c.id}" tabindex="0"
      role="button" aria-label="${esc(lines.join(' '))} — ${esc(rows.join(', '))}">
    <circle class="body" cx="${x}" cy="${y}" r="${r}" fill="var(--panel2)"
      stroke="${stroke}" stroke-width="${big?1.5:1.2}"${dash}/>
    ${ring}${title}${stat}
  </g>`;
}

const TITLES = {requirements_bot:['Requirements','Bot'],
  architecture_agent:['Architect','Bot'], builder_agent:['Builder','Bot'],
  evaluation_agent:['Tester/Fixer','Bot']};

function taskLabel(n){
  return n === 0 ? 'Idle' : n === 1 ? '1 running task' : `${n} running tasks`;
}

let graphSig = '';
function renderGraph(){
  if(!sys) return;
  const wrap = $('#graph-wrap'), svg = $('#graph');
  const W = Math.max(560, wrap.clientWidth), H = Math.max(380, wrap.clientHeight);
  // Rebuild ONLY when something visible changed. The page polls every 5s,
  // and rebuilding the rings restarts their orbit from the top — that was
  // the "glitch": a smooth roll snapped back on every poll.
  const sig = JSON.stringify([W, H, sys.active_runs.length,
    sys.health.supervisor_mode,
    sys.capabilities.map(c => [c.id, !!c.enabled, !!c.planned])]);
  if(sig === graphSig) return;
  graphSig = sig;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const cx = W/2, cy = H/2;
  // measured fit: shrink radii together if the workspace is small
  let rC = 134, rN = 64, margin = 30;
  let ring = Math.min(W, H)/2 - rN - margin;
  const need = rC + rN + 46;
  if(ring < need){
    const k = Math.max(.5, ring/need);
    rC *= k; rN *= k;
    ring = Math.min(W, H)/2 - rN - margin;
  }
  const caps = sys.capabilities.filter(c => c.id !== 'ai_supervisor' && !c.hidden);
  const nActive = sys.active_runs.length;
  const edges = [], nodes = [], ringsArr = [], pillsArr = [];
  let reqPos = null;
  caps.forEach((c,i) => {
    const a = (-90 + i*360/caps.length) * Math.PI/180;
    const x = cx + ring*Math.cos(a), y = cy + ring*Math.sin(a);
    const isReq = c.id === 'requirements_bot';
    if(isReq) reqPos = {x, y, r: rN};
    const working = isReq && nActive > 0;
    edges.push(`<line class="gedge${working?' active':''}${c.planned?' planned':''}"
      x1="${cx + rC*Math.cos(a)}" y1="${cy + rC*Math.sin(a)}"
      x2="${cx + (ring-rN)*Math.cos(a)}" y2="${cy + (ring-rN)*Math.sin(a)}"/>`);
    const status = c.planned ? 'Planned'
      : isReq ? taskLabel(nActive)
      : (c.enabled ? 'Idle' : 'Disabled');
    const dot = c.planned ? 'var(--muted)' : working ? 'var(--accent)'
      : c.enabled ? 'var(--green)' : 'var(--muted)';
    const ringCls = c.planned ? null : working ? 'running'
      : c.enabled ? 'idle' : null;
    if(ringCls) ringsArr.push({x, y, r: rN, cls: ringCls});
    pillsArr.push({x, y: y + rN, text: status, dot});
    nodes.push(graphNode(c, x, y, rN, TITLES[c.id]||[c.name], status, dot,
                         false, ringCls));
  });
  const centerRing = nActive ? 'center-active' : 'center-idle';
  ringsArr.push({x: cx, y: cy, r: rC, cls: centerRing});
  pillsArr.push({x: cx, y: cy + rC,
    text: nActive ? 'Working' : 'Idle',
    dot: nActive ? 'var(--accent)' : 'var(--green)'});
  // Supervisor status lives under the AI launcher (bottom-right), not here.
  const center = graphNode({id:'orchestrator', enabled:true}, cx, cy, rC,
    ['BotBot','Orchestrator'],
    [`controller: ${nActive ? 'executing' : 'idle'}`],
    nActive ? 'var(--accent)' : 'var(--green)', true, centerRing);
  // RequirementsBotEntryAction: New Session pill pinned under the node
  let entry = '';
  if(reqPos){
    // sits below the status pill (which overlaps the border at y + r)
    const bw = 108, bh = 26, bx = reqPos.x - bw/2, by = reqPos.y + reqPos.r + 20;
    entry = `<g class="gbtn" id="new-session-btn" tabindex="0" role="button"
        aria-label="Start a new interview session">
      <rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="13"/>
      <text x="${reqPos.x}" y="${by + bh/2 + 4}" text-anchor="middle">+ New Session</text>
    </g>`;
  }
  svg.innerHTML = edges.join('') + nodes.join('') + center + entry;
  // Lay the comet-tail overlay rings over the SVG nodes, mapping viewBox
  // units through the SVG's actual on-screen scale and letterbox offset
  // (preserveAspectRatio "meet"), so rings stay glued to their circles at
  // every pane size.
  wrap.querySelectorAll('.gring').forEach(el => el.remove());
  const ew = svg.clientWidth || W, eh = svg.clientHeight || H;
  const s = Math.min(ew/W, eh/H);
  const ox = (ew - W*s)/2, oy = (eh - H*s)/2;
  for(const g of ringsArr){
    const R = (g.r + 4) * s;   // band hugs the border just outside the stroke
    const d = document.createElement('div');
    d.className = 'gring ' + g.cls;
    d.style.cssText = `left:${(ox + g.x*s - R).toFixed(1)}px;`
                    + `top:${(oy + g.y*s - R).toFixed(1)}px;`
                    + `width:${(2*R).toFixed(1)}px;height:${(2*R).toFixed(1)}px`;
    wrap.appendChild(d);
  }
  // frosted-glass status pills, centered on each node's bottom border;
  // font scales with the map (floored for readability) so pills keep
  // their proportion to the circles on small windows
  wrap.querySelectorAll('.gpill').forEach(el => el.remove());
  const pf = Math.max(9, 10.5 * s);
  for(const g of pillsArr){
    const d = document.createElement('div');
    d.className = 'gpill';
    d.innerHTML = `<span class="dot" style="color:${g.dot}">●</span> ${esc(g.text)}`;
    d.style.cssText = `left:${(ox + g.x*s).toFixed(1)}px;`
                    + `top:${(oy + g.y*s).toFixed(1)}px;`
                    + `font-size:${pf.toFixed(1)}px;`
                    + `padding:${(2.5*Math.max(.8,s)).toFixed(1)}px ${(11*Math.max(.8,s)).toFixed(1)}px`;
    wrap.appendChild(d);
  }
  svg.querySelectorAll('.gnode').forEach(g => {
    const go = () => {
      const cap = g.dataset.cap;
      if(cap === 'requirements_bot') showView('flows');
      else if(cap === 'orchestrator') openOverlay('#sup-drawer', '#mgmt-input');
    };
    g.onclick = go;
    g.onkeydown = e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); go(); } };
  });
  const nb = svg.querySelector('#new-session-btn');
  if(nb){
    // Opens the CLIENT experience in a fresh tab: /chat?new=1 rotates the
    // client session cookie so the first message starts a brand-new
    // interview (the previous client session's records are preserved).
    const start = (e) => { e.stopPropagation(); window.open('/chat?new=1', '_blank'); };
    nb.onclick = start;
    nb.onkeydown = e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); start(e); } };
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
document.addEventListener('keydown', e => { if(e.key === 'Escape') closeOverlays(); });

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

/* ================= Chatbot Flows (monitoring, read-only) ================= */
const flowsUI = {data:null, expanded:new Set(), tab:{}, detail:{}};

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

async function loadFlows(){
  try{ flowsUI.data = await (await fetch('/api/flows')).json(); }
  catch(e){ return; }
  renderFlows();
  // refresh any open detail panels from records (read-only, no side effects)
  flowsUI.expanded.forEach(pid => loadFlowDetail(pid));
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
  if(!flowsUI.data) return;
  const q = '', filt = 'all';
  const tpl = flowsUI.data.template.stages;
  const flows = flowsUI.data.flows.filter(f => flowMatches(f, q, filt));
  const list = $('#flows-list');
  if(!flows.length){
    list.innerHTML = `<div class="flow-empty">${flowsUI.data.flows.length
      ? 'No flows match the current search/filter.'
      : 'No chatbot requests yet. Use New Session on Home or Copy Client Link in the sidebar.'}</div>`;
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
      // three simple statuses: the CURRENT step reads Running (with its
      // spinner) for the whole time the flow sits on it; the comet ring
      // still marks actual model execution. Details stay in the tooltip
      // and detail tabs.
      const simple = s.status==='completed' ? 'Completed'
        : (s.status==='current' || s.status==='blocked') ? 'Running' : 'Pending';
      const stSpin = (s.status==='current' || s.status==='blocked')
        ? '<span class="spin"></span>' : '';
      return `<div class="fc-step ${s.status}${busyCls}">
        <span class="cn" aria-hidden="true"><span class="ico">${STAGE_ICONS[t.id]||'•'}</span>
          <span class="numb">${badge}</span></span>
        <div><span class="lbl">${esc(t.label)}</span>
        ${showWho ? `<span class="who">${esc(t.who)}</span>` : ''}
        <span class="st" title="${esc(s.note||'')}">${stSpin}${simple}</span>
        ${m && m.calls ? `<span class="mline">
          <b>${m.cost_usd!=null ? '$'+m.cost_usd.toFixed(2) : 'cost —'}</b>
          · ${fmtSecs(m.active_seconds)} · ${esc(m.model||'—')}</span>` : ''}
        ${s.status==='current' && m && m.calls ? `<span class="mrow">
          <span class="mjson" title="Messages the bot sent · the client's average reply time">
            Sent <b>${m.msgs_sent ?? '—'}</b> · avg ${m.avg_client_seconds!=null
              ? fmtSecs(m.avg_client_seconds) : '—'}</span>
          <span class="mjson" title="Messages the client sent · the bot's average reply time">
            Received <b>${m.msgs_received ?? '—'}</b> · avg ${m.avg_bot_seconds!=null
              ? fmtSecs(m.avg_bot_seconds) : '—'}</span>
        </span>` : ''}
        ${t.id==='interviewing' && (f.head_rev || (m && m.calls)) ? `<span class="mrow">
          ${m && m.calls ? `<span class="mcol">
            <em>Input Tokens</em><b>${fmtTok(m.tokens_in)}</b>
            <span class="mjson" title="The interview conversation the bot works from"><a
              href="/api/transcript?project=${f.flow_id}" target="_blank">TXT</a></span>
          </span>` : ''}
          ${f.head_rev ? `<span class="mcol">
            <em>Output Tokens</em><b>${m && m.calls ? fmtTok(m.tokens_out) : '—'}</b>
            <span class="mjson" title="The requirements produced so far (revision r${f.head_rev})"><a
              href="/api/export?project=${f.flow_id}&format=legacy" target="_blank">JSON</a>/<a
              href="/api/export?project=${f.flow_id}&format=legacy&as=txt" target="_blank">TXT</a></span>
          </span>` : ''}
        </span>` : ''}
        </div></div>`;
    }).join('');
    const cur = tpl.find(t => t.id === f.current_stage);
    const curStage = f.stages.find(x => x.id === f.current_stage) || {};
    return `<div class="flow-card${flowsUI.expanded.has(f.flow_id)?' open':''}" data-fid="${f.flow_id}">
      <div class="fc-head" role="button" tabindex="0"
        aria-expanded="${flowsUI.expanded.has(f.flow_id)}"
        aria-label="Flow ${esc(f.name)} — expand details">
        <div class="fc-left">
          <span class="fc-flowstate">${f.finished
            ? `<span class="fs-done">✓</span> Completed`
            : `<span class="spin"></span>${esc(FLOW_STATE[f.current_stage]||'—')}`}</span>
          <span class="fc-idbig" title="${esc(f.flow_id)}">Project #${f.num ?? '—'}
            ${f.is_test ? `<span class="fc-test">Test</span>` : ''}</span>
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
      if(flowsUI.expanded.has(fid)){ flowsUI.expanded.delete(fid); card.classList.remove('open'); }
      else{ flowsUI.expanded.add(fid); card.classList.add('open'); loadFlowDetail(fid); }
      h.setAttribute('aria-expanded', card.classList.contains('open'));
    };
    h.onclick = toggle;
    h.onkeydown = e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(); } };
  });
  flowsUI.expanded.forEach(fid => renderFlowDetail(fid));
}

async function loadFlowDetail(fid){
  try{
    flowsUI.detail[fid] = await (await fetch('/api/flow?project='+fid)).json();
    renderFlowDetail(fid);
  }catch(e){}
}

function renderFlowDetail(fid){
  // Activity only: the recorded events and attempts for this flow.
  const el = document.getElementById('fd-' + fid);
  const d = flowsUI.detail[fid];
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

loadSystem();
setInterval(() => {
  if(document.body.className === 'view-home') loadSystem();
  if(document.body.className === 'view-flows') loadFlows();
}, 5000);
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


def system_payload() -> dict:
    store.ensure_default_project()
    return {
        "stats": store.system_stats(),
        "active_runs": controller.active_runs(),
        "capabilities": registry.capabilities(),
        "health": store.health() | {
            "last_call": store.last_provider_event(),
            "supervisor_mode": supervisor.mode()},
        "client_link": {"path": "/chat", "local_only": True},
    }


def flows_payload() -> dict:
    """Read-model for the Chatbot Flows monitor: one deterministic projection
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


def flow_detail(pid: str) -> dict:
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


def save_uploads(pid: str, files: list) -> dict:
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
        store.append_event(pid, "materials.uploaded", "owner", {"files": saved})
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
                    "Client " + time.strftime("%Y-%m-%d %H:%M"))["id"]
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

        # owner API (owner cookie required — deny by default)
        if not self._is_owner():
            self._json({"error": "owner authorization required"}, status=403)
            return
        pid = _project_of(q.get("project"))
        if route == "/api/system":
            self._json(system_payload())
        elif route == "/api/flows":
            self._json(flows_payload())
        elif route == "/api/flow":
            self._json(flow_detail(pid))
        elif route == "/api/projects":
            self._json([{"id": p["id"], "name": p["name"], "state": p["state"]}
                        for p in store.list_projects()])
        elif route == "/api/attention":
            self._json(store.open_reviews_all())
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
        if self.path == "/client/send":
            pid = self._client_pid()
            extra = None
            if pid is None:
                # First message: create the isolated interview exactly once
                # and issue the session. Merely loading the page never does.
                name = "Client " + time.strftime("%Y-%m-%d %H:%M")
                pid = store.create_project(name)["id"]
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
            out = save_uploads(pid, req.get("files") or [])
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
