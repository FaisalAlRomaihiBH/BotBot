# dashboard.py — the Orchestrator dashboard (serves http://localhost:8500).
#
# Views: Home (orchestrator graph, supervisor chat, needs-attention),
# Requirements Bot Chat (the customer interview), Contracts & Review
# (revisions, readiness, approval, export). All state comes from the
# orchestrator store; the page polls /api/home every 5 seconds.
#
# The persona-testing / prompt-improvement machinery that used to live here
# was removed on purpose: evaluation and improvement will be their own bots
# connected to the Orchestrator, not a dashboard feature.
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(__import__("os").environ.get("PORT", 8500))

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BotBot — Orchestrator</title>
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
#sidebar{width:190px;flex:none;background:var(--panel);border-right:1px solid var(--border);
  display:flex;flex-direction:column;transition:width .15s ease;overflow:hidden}
#sidebar.collapsed{width:44px}
#sb-head{display:flex;align-items:center;gap:8px;padding:12px 12px;border-bottom:1px solid var(--border)}
#sb-logo{width:20px;height:20px;flex:none;border:1px solid var(--border-hi);border-radius:5px;
  display:grid;place-items:center;font:600 10px var(--mono);color:var(--accent)}
#sb-title{font-weight:600;font-size:13px;white-space:nowrap}
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
#run-actions{margin-left:auto;display:flex;gap:6px}
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
#chat,#home,#review{display:none}
body.view-chat #chat{display:flex}
body.view-home #home{display:flex}
body.view-review #review{display:flex}
#home,#review{flex-direction:column;margin:14px 20px 20px;gap:14px;min-height:0}

/* ---------- chat ---------- */
#chat{flex:1;flex-direction:column;margin:14px 20px 20px;min-height:0;
  background:var(--panel);border:1px solid var(--border);border-radius:6px}
#chat-head{display:flex;align-items:center;gap:10px;padding:9px 14px;
  border-bottom:1px solid var(--border)}
#chat-head .t{font:600 11px var(--sans);text-transform:uppercase;
  letter-spacing:.07em;color:var(--text2)}
#chat-head .m{font:10.5px var(--mono);color:var(--muted)}
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

/* ---------- Home (orchestrator graph) & Contracts/Review views ---------- */
#proj-bar{display:flex;align-items:center;gap:10px;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;padding:8px 14px;flex-wrap:wrap}
#proj-bar .pb-label{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted)}
#proj-select{background:var(--panel2);border:1px solid var(--border);color:var(--text);
  border-radius:5px;padding:4px 8px;font:12px var(--sans);max-width:280px}
#home-stale{margin-left:auto;font:10.5px var(--mono);color:var(--muted)}
#home-stale.bad{color:var(--red)}
.h-card{background:var(--panel);border:1px solid var(--border);border-radius:6px;
  display:flex;flex-direction:column;min-width:0}
#graph-card{width:100%;max-width:960px;margin:0 auto}
.op-head{display:flex;align-items:center;gap:8px;padding:9px 14px;
  border-bottom:1px solid var(--border);font:600 11px var(--sans);
  text-transform:uppercase;letter-spacing:.07em;color:var(--text2)}
#graph{width:100%;height:auto;display:block}
#graph-note{padding:6px 14px 10px;font:10px var(--mono);color:var(--muted);
  text-align:center}
.gnode{cursor:pointer}
.gnode:focus{outline:none}
.gnode:focus>circle{stroke:var(--accent)}
.gnode.planned{cursor:default;opacity:.6}
.gnode text{font-family:var(--sans)}
.gedge{stroke:var(--border);stroke-width:1.2}
.gedge.planned{stroke-dasharray:2 5;opacity:.5}
.gedge.active{stroke:var(--accent);stroke-dasharray:6 6;animation:dashmove 1s linear infinite}
@keyframes dashmove{to{stroke-dashoffset:-12}}
@media (prefers-reduced-motion:reduce){.gedge.active{animation:none}}

/* ---------- floating supervisor chat ---------- */
#sup-fab{position:fixed;right:22px;bottom:22px;z-index:60;width:48px;height:48px;
  border-radius:50%;background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);
  font:600 13px var(--sans);cursor:pointer;display:grid;place-items:center;
  box-shadow:0 6px 24px rgba(0,0,0,.5)}
#sup-fab:hover{border-color:var(--accent)}
#sup-fab .fab-dot{position:absolute;top:3px;right:3px;width:9px;height:9px;
  border-radius:50%;border:2px solid var(--bg);background:var(--muted)}
#sup-fab .fab-dot.on{background:var(--green)}
#sup-drawer{position:fixed;right:22px;bottom:80px;z-index:60;width:350px;
  max-width:calc(100vw - 44px);height:460px;max-height:calc(100vh - 120px);
  background:var(--panel);border:1px solid var(--border);border-radius:8px;
  display:none;flex-direction:column;box-shadow:0 14px 44px rgba(0,0,0,.55)}
#sup-drawer.open{display:flex}
#sup-drawer .op-head{border-radius:8px 8px 0 0}
#sup-close{margin-left:6px;background:none;border:none;color:var(--muted);
  cursor:pointer;font-size:14px}
#sup-close:hover{color:var(--text)}
#mgmt-thread{flex:1;overflow-y:auto;padding:12px 14px;display:flex;
  flex-direction:column;gap:9px;min-height:160px}
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
#attention-list{padding:10px 14px;display:flex;flex-direction:column;gap:8px}
.att-item{display:flex;align-items:flex-start;gap:10px;border:1px solid var(--border);
  border-radius:6px;padding:8px 12px;font-size:12.5px;background:var(--panel2)}
.att-item .blk{flex:none;font:600 9px var(--mono);text-transform:uppercase;
  letter-spacing:.05em;padding:2px 7px;border-radius:99px;margin-top:1px}
.att-item .blk.b1{color:var(--red);border:1px solid #552b2b}
.att-item .blk.b0{color:var(--amber);border:1px solid #5c4a1e}
.att-item .txt{flex:1;line-height:1.5}
.att-item .meta{font:10px var(--mono);color:var(--muted)}
.att-empty{color:var(--muted);font-size:12px;padding:4px 0}
.rv-card{background:var(--panel);border:1px solid var(--border);border-radius:6px}
.rv-body{padding:10px 14px;font-size:12.5px;line-height:1.6}
.rv-body table{width:100%;border-collapse:collapse;font-size:12px}
.rv-body td,.rv-body th{padding:4px 8px;border-bottom:1px solid var(--border);
  text-align:left;vertical-align:top}
.rv-body th{font:600 10px var(--sans);text-transform:uppercase;
  letter-spacing:.06em;color:var(--muted)}
#rv-actions{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--border);
  flex-wrap:wrap;align-items:center}
.stage-chip{font:600 9.5px var(--mono);padding:2px 8px;border-radius:99px;
  border:1px solid var(--border);color:var(--muted);margin-right:6px}
.stage-chip.on{color:var(--green);border-color:#234534}
@media (max-width:760px){
  #sidebar{display:none}
}
</style></head><body class="view-home">
<div id="shell">
  <aside id="sidebar">
    <div id="sb-head"><div id="sb-logo">B</div><span id="sb-title">BotBot</span>
      <button id="sb-toggle" title="Collapse">⟨⟩</button></div>
    <nav id="sb-nav">
      <div class="nav-item active" id="nav-home" data-view="home"><span class="nav-ico">◎</span><span class="nav-label">Home</span></div>
      <div class="nav-item" id="nav-chat" data-view="chat"><span class="nav-ico">▶</span><span class="nav-label">Requirements Bot Chat</span></div>
      <div class="nav-item" id="nav-review" data-view="review"><span class="nav-ico">☑</span><span class="nav-label">Contracts &amp; Review</span></div>
    </nav>
    <div id="sb-foot">
      <div><span class="dot" id="dot-store"></span><span id="txt-store">store: checking…</span></div>
      <div><span class="dot" id="dot-provider"></span><span id="txt-provider">model: no calls yet</span></div>
      <div><span class="dot" id="dot-sup"></span><span id="txt-sup">supervisor: —</span></div>
    </div>
  </aside>

  <div id="main">
    <div id="run-header">
      <div><div id="run-title">Orchestrator</div></div>
      <div id="run-actions">
        <span id="hdr-project" style="font:11px var(--mono);color:var(--muted)"></span>
      </div>
    </div>

    <div id="home">
      <div id="proj-bar">
        <span class="pb-label">Project</span>
        <select id="proj-select" aria-label="Select project"></select>
        <button class="act" id="proj-new">+ New project</button>
        <span class="badge idle" id="proj-state"><span class="b-dot"></span><span id="proj-state-txt">—</span></span>
        <span id="home-stale"></span>
      </div>
      <div class="h-card" id="graph-card">
        <div class="op-head">Orchestrator map
          <span style="margin-left:auto;font:10px var(--mono);color:var(--muted);text-transform:none;letter-spacing:0">registry-driven · idle ≠ missing</span></div>
        <svg id="graph" viewBox="0 0 760 490" role="img" aria-label="Orchestrator graph"></svg>
        <div id="graph-note">Lines are controller-mediated communication. Dashed nodes are planned, not implemented.</div>
      </div>
      <div class="h-card" id="attention-card">
        <div class="op-head">Needs Attention</div>
        <div id="attention-list"></div>
      </div>
    </div>

    <div id="review">
      <div class="rv-card"><div class="op-head">Lifecycle</div>
        <div class="rv-body" id="rv-state"></div></div>
      <div class="rv-card"><div class="op-head">Readiness (computed rubric — interview completion is not readiness)</div>
        <div class="rv-body" id="rv-readiness"></div></div>
      <div class="rv-card"><div class="op-head">Review requests</div>
        <div class="rv-body" id="rv-reviews"></div></div>
      <div class="rv-card"><div class="op-head">Revisions &amp; approval</div>
        <div class="rv-body" id="rv-revisions"></div>
        <div id="rv-actions">
          <button class="act" id="rv-approve">Approve head revision</button>
          <button class="act" id="rv-export-legacy">Export brief (legacy)</button>
          <button class="act" id="rv-export-ext">Export extended package</button>
          <span id="rv-msg" style="font:11px var(--mono);color:var(--muted)"></span>
        </div></div>
    </div>

    <div id="chat">
      <div id="chat-head"><span class="t">Requirement Bot Chat</span>
        <span class="m">interactive interview · put files in uploads/ when asked</span>
        <button class="act" id="chat-reset" title="Start a new interview">↺ New interview</button></div>
      <div id="chat-thread"></div>
      <div id="chat-bar">
        <input type="file" id="chat-file" multiple hidden
               accept=".txt,.md,.csv,.png,.jpg,.jpeg,.webp,.gif">
        <button id="chat-attach" title="Attach chat exports / screenshots (saved to uploads/)">📎</button>
        <textarea id="chat-input" rows="1" placeholder="Type your answer… (Enter to send, Shift+Enter for newline)" spellcheck="false"></textarea>
        <button id="chat-send">Send</button>
      </div>
    </div>
  </div>
</div>

<button id="sup-fab" title="AI Supervisor" aria-label="Open AI supervisor chat"
  aria-expanded="false">AI<span class="fab-dot" id="fab-dot"></span></button>
<div id="sup-drawer" role="dialog" aria-label="AI supervisor chat">
  <div class="op-head">AI Supervisor
    <span class="badge idle" id="sup-mode"><span class="b-dot"></span><span id="sup-mode-txt">—</span></span>
    <button class="act" id="sup-toggle" style="margin-left:auto">…</button>
    <button id="sup-close" title="Close" aria-label="Close">✕</button></div>
  <div id="mgmt-thread"></div>
  <div id="mgmt-bar">
    <textarea id="mgmt-input" rows="1" placeholder="Ask the supervisor about this project… (a paid call when enabled)" spellcheck="false"></textarea>
    <button class="act" id="mgmt-send">Ask</button>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
$('#sb-toggle').onclick = () => $('#sidebar').classList.toggle('collapsed');
document.querySelectorAll('.nav-item[data-view]').forEach(item => item.onclick = () => {
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  item.classList.add('active');
  document.body.className = 'view-' + item.dataset.view;
  if(item.dataset.view === 'chat' && !chat.loaded) loadChat();
  if(item.dataset.view === 'home') loadHome();
  if(item.dataset.view === 'review') loadReview();
});

/* ---------- Requirement Bot chat ---------- */
const chat = {loaded:false, busy:false, complete:false};
function renderChat(d){
  chat.complete = d.complete;
  const t = $('#chat-thread');
  t.innerHTML = d.messages.map(m =>
    `<div class="msg ${m.role === 'human' ? 'human' : 'ai'}">
       <div class="who">${m.role === 'human' ? 'You' : 'RequirementsBot'}</div>${esc(m.text)}</div>`
  ).join('')
  + (d.error ? `<div class="msg err">${esc(d.error)}</div>` : '')
  + (d.saved ? `<div class="msg sys">✓ Interview complete — full brief saved to ${d.saved}</div>` : '')
  + (chat.busy ? `<div id="chat-typing"><span class="spin"></span>RequirementsBot is thinking…</div>` : '');
  t.scrollTop = t.scrollHeight;
  $('#chat-send').disabled = chat.busy || chat.complete;
  $('#chat-input').disabled = chat.complete;
}
async function loadChat(){
  chat.loaded = true;
  try{ renderChat(await (await fetch('/chat/history?project='+proj.id)).json()); }
  catch(e){ chat.loaded = false; }
}
async function sendChat(){
  const inp = $('#chat-input'), text = inp.value.trim();
  if(!text || chat.busy || chat.complete) return;
  chat.busy = true; inp.value = '';
  // optimistic echo while the bot works
  const t = $('#chat-thread');
  t.insertAdjacentHTML('beforeend',
    `<div class="msg human"><div class="who">You</div>${esc(text)}</div>
     <div id="chat-typing"><span class="spin"></span>RequirementsBot is thinking…</div>`);
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
        `<div class="msg sys">📎 Uploaded to uploads/: ${esc(d.saved.join(', '))} — now tell the bot the files are ready.</div>`);
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

/* ---------- Home: projects, orchestrator graph, supervisor chat ---------- */
const proj = {id:'default', name:'Default project', busy:false, mode:'disabled'};

function switchProject(id){
  proj.id = id; chat.loaded = false; mgmtCount = -1;
  loadHome();
  if(document.body.className === 'view-chat') loadChat();
  if(document.body.className === 'view-review') loadReview();
}

async function loadHome(){
  let d;
  try{ d = await (await fetch('/api/home?project='+proj.id)).json(); }
  catch(e){
    $('#home-stale').textContent = 'stale — server unreachable';
    $('#home-stale').className = 'bad';
    return;
  }
  $('#home-stale').textContent = 'updated just now';
  $('#home-stale').className = '';
  proj.name = d.project.name; proj.busy = d.project.busy;
  proj.mode = d.health.supervisor_mode;
  $('#hdr-project').textContent = proj.name + ' · ' + (d.project.state||'—');

  // project selector
  const sel = $('#proj-select');
  sel.innerHTML = d.projects.map(p =>
    `<option value="${p.id}"${p.id===proj.id?' selected':''}>${esc(p.name)} (${p.state})</option>`).join('');
  const stateCls = {interviewing:'running', review_required:'retrying',
                    approved:'completed', interview_closed:'retrying'}[d.project.state] || 'idle';
  $('#proj-state').className = 'badge ' + stateCls;
  $('#proj-state-txt').textContent = (d.project.state||'created').replace(/_/g,' ');

  // honest footer
  const h = d.health;
  $('#dot-store').className = 'dot ' + (h.store_ok ? 'ok' : 'bad');
  $('#txt-store').textContent = 'store: ' + (h.store_ok ? 'writable' : 'ERROR');
  const lc = h.last_call;
  $('#dot-provider').className = 'dot ' + (lc ? (lc.error ? 'bad' : 'ok') : '');
  $('#txt-provider').textContent = lc
    ? 'model: last ' + lc.purpose + (lc.error ? ' FAILED' : ' ok')
    : 'model: no calls yet';
  $('#dot-sup').className = 'dot ' + (proj.mode==='advisory' ? 'ok' : '');
  $('#txt-sup').textContent = 'supervisor: ' + proj.mode;

  // supervisor drawer header + floating button dot
  $('#sup-mode').className = 'badge ' + (proj.mode==='advisory' ? 'completed' : 'idle');
  $('#sup-mode-txt').textContent = proj.mode==='advisory' ? 'Advisory' : 'Disabled';
  $('#sup-toggle').textContent = proj.mode==='advisory' ? 'Disable' : 'Enable advisory mode';
  $('#fab-dot').className = 'fab-dot' + (proj.mode==='advisory' ? ' on' : '');

  renderGraph(d);
  renderAttention(d.attention);
  loadMgmt();
}

/* One node of the orchestrator graph: title + one status line inside the
   circle, nothing else — the circle IS the label, so no outside caption to
   duplicate it. Multi-word names stack as two centered lines. */
function graphNode(c, x, y, r, status, dotColor){
  const words = c.name.split(' ');
  const line1 = words[0], line2 = words.slice(1).join(' ');
  // vertical rhythm: title block centered slightly above middle, status below
  const titleY = line2 ? y - 8 : y - 3;
  const title = line2
    ? `<text x="${x}" y="${titleY}" text-anchor="middle" fill="var(--text)"
         font-size="10.5" font-weight="600">${esc(line1)}</text>
       <text x="${x}" y="${titleY+12}" text-anchor="middle" fill="var(--text)"
         font-size="10.5" font-weight="600">${esc(line2)}</text>`
    : `<text x="${x}" y="${titleY}" text-anchor="middle" fill="var(--text)"
         font-size="10.5" font-weight="600">${esc(line1)}</text>`;
  const stroke = c.planned ? 'var(--muted)' : (c.enabled ? 'var(--border-hi)' : 'var(--border)');
  const dash = c.planned ? ' stroke-dasharray="5 4"' : '';
  return `<g class="gnode${c.planned?' planned':''}" data-cap="${c.id}" tabindex="0"
      role="button" aria-label="${esc(c.name)} — ${status}">
    <circle cx="${x}" cy="${y}" r="${r}" fill="var(--panel2)" stroke="${stroke}"${dash}/>
    ${title}
    <text x="${x}" y="${y+18}" text-anchor="middle" fill="var(--muted)"
      font-size="8.5" letter-spacing=".04em">${esc(status.toUpperCase())}</text>
    <circle cx="${x}" cy="${y-r+9}" r="3" fill="${dotColor}"/>
  </g>`;
}

function renderGraph(d){
  const caps = d.capabilities;
  // Deterministic radial layout: center node at the exact canvas center,
  // outer nodes evenly spaced by angle on one invisible ring, first at 12
  // o'clock. The SVG scales as one unit, so it stays centered responsively.
  const W=760, H=490, cx=W/2, cy=H/2, RING=168, R_CENTER=82, R_NODE=46;
  const nodes = caps.filter(c => c.id !== 'ai_supervisor');
  const edges = [], circles = [];
  nodes.forEach((c,i) => {
    const a = (-90 + i*360/nodes.length) * Math.PI/180;
    const x = cx + RING*Math.cos(a), y = cy + RING*Math.sin(a);
    const active = c.id==='requirements_bot' && d.project.busy;
    // trim edges to the circle borders so lines don't pierce the nodes
    const x1 = cx + R_CENTER*Math.cos(a), y1 = cy + R_CENTER*Math.sin(a);
    const x2 = cx + (RING-R_NODE)*Math.cos(a), y2 = cy + (RING-R_NODE)*Math.sin(a);
    edges.push(`<line class="gedge${active?' active':''}${c.planned?' planned':''}"
      x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`);
    const status = c.planned ? 'Planned' : active ? 'Working'
                 : (c.enabled ? 'Idle' : 'Disabled');
    const dot = c.planned ? 'var(--muted)' : active ? 'var(--accent)'
              : c.enabled ? 'var(--green)' : 'var(--muted)';
    circles.push(graphNode(c, x, y, R_NODE, status, dot));
  });
  const supTxt = 'supervisor: ' + (proj.mode==='advisory' ? 'advisory' : 'disabled');
  const busyTxt = 'controller: ' + (d.project.busy ? 'executing' : 'idle');
  const center = `<g class="gnode" data-cap="orchestrator" tabindex="0" role="button"
      aria-label="Orchestrator — controller and AI supervisor">
    <circle cx="${cx}" cy="${cy}" r="${R_CENTER}" fill="var(--panel2)"
      stroke="var(--accent)" stroke-width="1.4"/>
    <text x="${cx}" y="${cy-10}" text-anchor="middle" fill="var(--text)"
      font-size="16" font-weight="700">Orchestrator</text>
    <text x="${cx}" y="${cy+12}" text-anchor="middle" fill="var(--text2)"
      font-size="9.5">${busyTxt}</text>
    <text x="${cx}" y="${cy+27}" text-anchor="middle" fill="var(--muted)"
      font-size="9.5">${supTxt}</text>
  </g>`;
  const svg = $('#graph');
  svg.innerHTML = edges.join('') + circles.join('') + center;
  svg.querySelectorAll('.gnode').forEach(g => {
    const go = () => {
      const cap = g.dataset.cap;
      if(cap === 'requirements_bot') $('#nav-chat').click();
      else if(cap === 'orchestrator') openSupervisor();
    };
    g.onclick = go;
    g.onkeydown = e => { if(e.key==='Enter'||e.key===' '){ e.preventDefault(); go(); } };
  });
}

function renderAttention(items){
  $('#attention-list').innerHTML = (items && items.length)
    ? items.map(rv => `<div class="att-item">
        <span class="blk b${rv.blocking?1:0}">${rv.blocking?'blocking':'review'}</span>
        <span class="txt">${esc(rv.decision_needed)}
          <div class="meta">rev ${rv.revision??'—'} · ${rv.source} · #${rv.id}</div></span>
        <button class="act" data-rid="${rv.id}">Resolve…</button>
      </div>`).join('')
    : `<div class="att-empty">Nothing needs attention for this project.</div>`;
  document.querySelectorAll('#attention-list [data-rid]').forEach(b => b.onclick = async () => {
    const disp = prompt('Disposition (what was decided and why):');
    if(!disp) return;
    await fetch('/api/review/resolve', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project: proj.id, review_id: +b.dataset.rid, disposition: disp})});
    loadHome();
  });
}

/* ---------- floating supervisor chat (drawer) ---------- */
function openSupervisor(){
  $('#sup-drawer').classList.add('open');
  $('#sup-fab').setAttribute('aria-expanded', 'true');
  loadMgmt();
  $('#mgmt-input').focus();
}
function closeSupervisor(){
  $('#sup-drawer').classList.remove('open');
  $('#sup-fab').setAttribute('aria-expanded', 'false');
}
$('#sup-fab').onclick = () =>
  $('#sup-drawer').classList.contains('open') ? closeSupervisor() : openSupervisor();
$('#sup-close').onclick = closeSupervisor;
document.addEventListener('keydown', e => {
  if(e.key === 'Escape' && $('#sup-drawer').classList.contains('open')) closeSupervisor();
});

/* ---------- supervisor management chat ---------- */
let mgmtCount = -1;
async function loadMgmt(){
  try{
    const d = await (await fetch('/api/management?project='+proj.id)).json();
    const n = (d.messages||[]).length;
    if(n === mgmtCount) return;   // don't clobber the thread on every poll
    mgmtCount = n;
    renderMgmt(d);
  }catch(e){}
}
function renderMgmt(d){
  const t = $('#mgmt-thread');
  t.innerHTML = (d.messages||[]).map(m =>
    `<div class="mmsg ${m.role}"><div class="who">${m.role}</div>${esc(m.text)}</div>`).join('')
    || `<div class="mmsg system">No management conversation yet. Ask the supervisor about this project — it reads recorded state only and cannot change anything.</div>`;
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
      body: JSON.stringify({project: proj.id, text})})).json();
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
  const enabling = proj.mode !== 'advisory';
  if(enabling && !confirm('Enable the AI supervisor (advisory mode)? Each question you ask it becomes a paid model call.')) return;
  await fetch('/api/supervisor/enable', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({enabled: enabling})});
  loadHome();
};
$('#proj-select').onchange = e => switchProject(e.target.value);
$('#proj-new').onclick = async () => {
  const name = prompt('New project name (one project per business interview):');
  if(!name) return;
  const d = await (await fetch('/api/projects', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name})})).json();
  if(d.id) switchProject(d.id);
};

/* ---------- Contracts & Review ---------- */
async function loadReview(){
  let d;
  try{ d = await (await fetch('/api/review?project='+proj.id)).json(); }
  catch(e){ return; }
  const stages = ['interviewing','interview_closed','review_required','approved'];
  const idx = stages.indexOf(d.state);
  $('#rv-state').innerHTML =
    stages.map((s,i) => `<span class="stage-chip${i<=idx?' on':''}">${s.replace(/_/g,' ')}</span>`).join('')
    + `<div style="margin-top:8px;color:var(--muted);font-size:11.5px">
       Interview closed, ready for review, and approved are three different facts —
       a finished interview is not an approved brief.</div>`;
  $('#rv-readiness').innerHTML = (d.readiness && d.readiness.length)
    ? `<table><tr><th>Gap</th><th>Blocking</th><th>Why</th></tr>` +
      d.readiness.map(g => `<tr><td>${esc(g.name)}</td>
        <td>${g.blocking?'<span style="color:var(--red)">yes</span>':'no'}</td>
        <td>${esc(g.why)}</td></tr>`).join('') + `</table>`
    : `<span style="color:var(--muted)">No revision yet — nothing to evaluate.</span>`;
  $('#rv-reviews').innerHTML = (d.reviews && d.reviews.length)
    ? `<table><tr><th>#</th><th>Status</th><th>Decision needed</th><th>Disposition</th></tr>` +
      d.reviews.map(r => `<tr><td>${r.id}</td><td>${r.status}${r.blocking?' · blocking':''}</td>
        <td>${esc(r.decision_needed)}</td><td>${esc(r.disposition||'—')}</td></tr>`).join('') + `</table>`
    : `<span style="color:var(--muted)">No review requests yet.</span>`;
  $('#rv-revisions').innerHTML =
    (d.approval ? `<div style="color:var(--green);margin-bottom:8px">✓ rev ${d.approval.revision}
       approved by ${esc(d.approval.actor)} — ${esc(d.approval.reason||'')}</div>` : '')
    + ((d.revisions && d.revisions.length)
      ? `<table><tr><th>Rev</th><th>Size</th><th>Committed</th></tr>` +
        d.revisions.map(r => `<tr><td>r${r.rev}${r.rev===d.head?' (head)':''}</td>
          <td>${(r.size/1024).toFixed(1)} kB</td>
          <td>${new Date(r.created_ts*1000).toLocaleString()}</td></tr>`).join('') + `</table>`
      : `<span style="color:var(--muted)">No revisions committed yet.</span>`);
  $('#rv-approve').disabled = !d.head;
  $('#rv-approve').onclick = async () => {
    const reason = prompt(`Approve revision r${d.head} for handoff? State the reason:`);
    if(!reason) return;
    const res = await (await fetch('/api/approve', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({project: proj.id, revision: d.head, reason})})).json();
    $('#rv-msg').textContent = res.error || ('approved r' + res.revision);
    loadReview();
  };
  $('#rv-export-legacy').onclick = () =>
    window.open('/api/export?project='+proj.id+'&format=legacy');
  $('#rv-export-ext').onclick = () =>
    window.open('/api/export?project='+proj.id+'&format=extended');
}
loadHome();
setInterval(() => { if(document.body.className === 'view-home') loadHome(); }, 5000);
</script></body></html>"""


# ---------- orchestrated chat: every interview is a project in the store ----
# The legacy /chat/* routes keep their request/response shapes but are served
# by the execution controller against the selected project (default: the
# auto-created "default" project, which preserves the old single-session
# behavior including the shared uploads/ folder and requirements_brief.json).
from orchestrator import controller, registry, store, supervisor  # noqa: E402


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
    if "messages" not in out:  # lock/validation errors still show the thread
        out = chat_history(pid) | out
    return out


def home_payload(pid: str) -> dict:
    project = store.get_project(pid) or {}
    return {
        "project": {"id": pid, "name": project.get("name"),
                    "state": project.get("state"),
                    "busy": controller.is_busy(pid)},
        "projects": [{"id": p["id"], "name": p["name"], "state": p["state"]}
                     for p in store.list_projects()],
        "capabilities": registry.capabilities(),
        "attention": store.open_reviews(pid),
        "usage": store.usage_totals(pid),
        "health": store.health() | {
            "last_call": store.last_provider_event(),
            "supervisor_mode": supervisor.mode()},
        "events": store.recent_events(pid, 12),
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


def chat_upload(pid: str, files: list) -> dict:
    """Save attached files into the PROJECT's uploads dir for the bot's scan."""
    import base64
    updir = controller.uploads_dir(pid)
    updir.mkdir(parents=True, exist_ok=True)
    saved, rejected = [], []
    for f in files[:20]:
        name = Path(str(f.get("name", ""))).name  # strip any path components
        ext = Path(name).suffix.lower()
        if not name or ext not in ALLOWED_EXTS:
            rejected.append(f"{name or '(unnamed)'} — only "
                            + " ".join(sorted(ALLOWED_EXTS)) + " are readable")
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


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(json.dumps(obj, default=str).encode("utf-8"),
                   "application/json")

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        pid = _project_of(q.get("project"))
        route = u.path
        if route == "/chat/history":
            self._json(chat_history(pid))
        elif route == "/api/home":
            self._json(home_payload(pid))
        elif route == "/api/review":
            self._json(review_payload(pid))
        elif route == "/api/management":
            self._json(supervisor.management_payload(pid))
        elif route == "/api/export":
            out = controller.export_package(pid, q.get("format", "legacy"))
            self._send(json.dumps(out, indent=2, ensure_ascii=False,
                                  default=str).encode("utf-8"),
                       "application/json",
                       {"Content-Disposition":
                        f'attachment; filename="{pid}_brief.json"'})
        else:
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 32 * 1024 * 1024:
            self._json({"error": "request too large"})
            return
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}
        pid = _project_of(req)
        if self.path == "/chat/send":
            out = chat_send(pid, str(req.get("message", "")).strip())
        elif self.path == "/chat/upload":
            out = chat_upload(pid, req.get("files") or [])
        elif self.path == "/chat/reset":
            out = controller.reset_interview(pid)
        elif self.path == "/api/projects":
            name = str(req.get("name", "")).strip()[:80]
            out = store.create_project(name) if name else {"error": "name required"}
        elif self.path == "/api/management":
            out = supervisor.ask(pid, str(req.get("text", "")).strip()[:4000])
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
    print(f"Dashboard: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
