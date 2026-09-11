# dashboard.py — live localhost dashboard for parallel persona runs.
#
# Run:  python dashboard.py            (serves http://localhost:8500)
# Shows the latest run in parallel_runs/ as a 5-stage pipeline:
#   Persona Generator -> Interviewing -> Judge -> Improver -> Fixing Code
# All data is parsed live from run.log; the page polls every 2 seconds.
import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
PORT = int(__import__("os").environ.get("PORT", 8500))

# "in 45,120 ($0.12) out 3,240 ($0.08) total $0.20" -> stat-box numbers
IO_RE = re.compile(r"in ([\d,]+) \(\$([\d.]+)\) out ([\d,]+) \(\$([\d.]+)\) "
                   r"total \$([\d.]+)")


def parse_io(text: str):
    m = IO_RE.search(text)
    if not m:
        return None
    return {"in_tok": m.group(1), "in_usd": m.group(2),
            "out_tok": m.group(3), "out_usd": m.group(4), "total": m.group(5)}


def _num(tok) -> int:
    try:
        return int(str(tok).replace(",", ""))
    except (ValueError, TypeError):
        return 0


def _usd(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


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
                "run_finished": False, "log": "", "started_at": None,
                "last_activity": None}

    log_file = run / "run.log"
    log_text = log_file.read_text(encoding="utf-8", errors="replace") \
        if log_file.exists() else "(this run has no run.log — older run)"

    # run dir name is a timestamp: 20260911_033231
    started_at = None
    try:
        started_at = datetime.strptime(run.name, "%Y%m%d_%H%M%S").timestamp()
    except ValueError:
        pass
    last_activity = log_file.stat().st_mtime if log_file.exists() else None

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
                                         "bot_cost": None, "persona_cost": None,
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
        if "] cost: $" in line:
            m = re.search(r"\(bot [\w.-]+ \$([\d.]+) \+ persona [\w.-]+ "
                          r"\$([\d.]+)", line)
            if m:
                iv["bot_cost"], iv["persona_cost"] = m.group(1), m.group(2)
        if "FAILED" in line:
            iv["status"] = "failed"

    return {"run_dir": run.name, "log": log_text[-40000:],
            "generator": generator, "pipeline": pipeline,
            "run_finished": run_finished,
            "started_at": started_at, "last_activity": last_activity,
            "judges": sorted(judges.values(), key=lambda j: j["index"]),
            "interviews": sorted(interviews.values(), key=lambda i: i["index"])}


def _model_name(raw):
    if not raw:
        return None
    name = raw.replace("claude-", "").replace("-", " ").strip()
    return ("Claude " + name.title()) if name else None


def _stage(num, sid, name, desc, usage):
    return {"id": sid, "number": num, "name": name, "description": desc,
            "status": "pending", "model": None, "usage": usage,
            "inputTokens": 0, "outputTokens": 0, "cost": 0.0,
            "progress": None, "latestActivity": None, "logs": []}


def build_lanes(d: dict) -> list[dict]:
    """One lane per persona: its own Interviewing -> Judge path."""
    lanes = []
    g = d.get("generator") or {}
    n = max(1, len(d["interviews"]))
    for iv in d["interviews"]:
        idx = iv["index"]
        j = next((x for x in d["judges"] if x["index"] == idx), None)

        # per-lane share of the batched generator call (1 call for all personas)
        pst = _stage(1, f"persona-{idx}", "Persona",
                     f"Batched generation (1/{n} share)", "Claude API")
        pst["status"] = "completed" if g.get("done") else "running"
        pst["model"] = _model_name(g.get("model"))
        if g.get("io"):
            pst["inputTokens"] = _num(g["io"]["in_tok"]) // n
            pst["outputTokens"] = _num(g["io"]["out_tok"]) // n
            pst["cost"] = _usd(g["io"]["total"]) / n
        pst["latestActivity"] = f"Generated in one batch call with {n} persona(s)"

        ist = _stage(2, f"interview-{idx}", "Interviewing",
                     iv["industry"], "Claude API")
        ist["status"] = ("running" if iv["status"] == "interviewing"
                         else "failed" if iv["status"] == "failed" else "completed")
        ist["model"] = _model_name(iv.get("bot_model"))
        if iv.get("io"):
            ist["inputTokens"] = _num(iv["io"]["in_tok"])
            ist["outputTokens"] = _num(iv["io"]["out_tok"])
            ist["cost"] = _usd(iv["io"]["total"])
        ist["progress"] = {"label": "turn", "value": iv["turn"]}
        ist["botCost"] = iv.get("bot_cost")
        ist["personaCost"] = iv.get("persona_cost")
        ist["latestActivity"] = (iv["log"][-1][:90] if iv["log"] else None)
        ist["logs"] = iv["log"][-40:]

        jst = _stage(3, f"judge-{idx}", "Judge",
                     "Evaluates requirements quality", "Claude API")
        if j:
            jst["status"] = "completed" if j.get("done") else "running"
            jst["model"] = _model_name(j.get("model"))
            if j.get("io"):
                jst["inputTokens"] = _num(j["io"]["in_tok"])
                jst["outputTokens"] = _num(j["io"]["out_tok"])
                jst["cost"] = _usd(j["io"]["total"])
            if j.get("done") and iv.get("score"):
                jst["latestActivity"] = (f"score {iv['score']}, "
                                         f"{iv['findings']} findings")
            elif jst["status"] == "running":
                jst["latestActivity"] = "Evaluating requirements…"
        elif ist["status"] == "running":
            jst["status"] = "queued"

        # per-lane share of the shared run-level Improver / Fixing Code steps
        shared = []
        for num, key, name, usage, run_note in (
                (4, "improve", "Improver", "Claude API", "Refining prompt…"),
                (5, "codefix", "Fixing Code", "Claude Membership",
                 "Applying code fixes…")):
            raw = d["pipeline"][key]
            st = _stage(num, f"{key}-{idx}", name,
                        f"Shared step (1/{n} share)", usage)
            st["status"] = {"run": "running", "done": "completed",
                            "failed": "failed"}.get(raw["status"], "pending")
            st["model"] = _model_name(raw.get("model"))
            if raw.get("io"):
                st["inputTokens"] = _num(raw["io"]["in_tok"]) // n
                st["outputTokens"] = _num(raw["io"]["out_tok"]) // n
                st["cost"] = _usd(raw["io"]["total"]) / n
            st["latestActivity"] = raw.get("note") or (
                run_note if st["status"] == "running" else None)
            shared.append(st)
        imp, fix = shared
        if jst["status"] == "completed" and imp["status"] == "pending":
            imp["status"] = "queued"
        if imp["status"] == "completed" and fix["status"] == "pending":
            fix["status"] = "queued"
        if fix["status"] == "completed":
            fix["name"] = "Completed Fixing Cycle"
            fix["latestActivity"] = (fix["latestActivity"]
                                     or "Cycle completed successfully.")

        lanes.append({"index": idx, "industry": iv["industry"],
                      "score": iv.get("score"),
                      "persona": pst, "interview": ist, "judge": jst,
                      "improve": imp, "codefix": fix})
    return lanes


def build_stages(d: dict) -> list[dict]:
    """Aggregate parsed run data into the 5 reusable stage objects."""
    interviews, judges = d["interviews"], d["judges"]
    g = d.get("generator") or {}
    p = d["pipeline"]
    stage = _stage

    s1 = stage(1, "persona", "Persona Generator",
               "Generates business-owner personas", "Claude API")
    s2 = stage(2, "interview", "Interviewing",
               "Bot interviews each persona", "Claude API")
    s3 = stage(3, "judge", "Judge", "Evaluates requirements quality", "Claude API")
    s4 = stage(4, "improve", "Improver", "Refines the interviewer prompt", "Claude API")
    s5 = stage(5, "codefix", "Fixing Code",
               "Applies code fixes for this cycle", "Claude Membership")

    # 1 — persona generator
    if g.get("model") or g.get("done") or interviews:
        s1["status"] = "completed" if g.get("done") else "running"
    s1["model"] = _model_name(g.get("model"))
    if g.get("io"):
        io = g["io"]
        s1["inputTokens"] = _num(io["in_tok"]); s1["outputTokens"] = _num(io["out_tok"])
        s1["cost"] = _usd(io["total"])
        s1["latestActivity"] = "Personas generated"
    elif s1["status"] == "running":
        s1["latestActivity"] = "Generating personas…"

    # 2 — interviewing (aggregate all interviews)
    if interviews:
        active = [iv for iv in interviews if iv["status"] == "interviewing"]
        s2["status"] = "running" if active else "completed"
        if any(iv["status"] == "failed" for iv in interviews):
            s2["status"] = "failed" if not active else "running"
        s2["model"] = _model_name(interviews[0].get("bot_model"))
        for iv in interviews:
            if iv.get("io"):
                s2["inputTokens"] += _num(iv["io"]["in_tok"])
                s2["outputTokens"] += _num(iv["io"]["out_tok"])
                s2["cost"] += _usd(iv["io"]["total"])
            if iv["log"]:
                s2["logs"] += [f"#{iv['index']} {l}" for l in iv["log"][-4:]]
        cur = active[0] if active else interviews[-1]
        s2["progress"] = {"label": "turn", "value": cur["turn"]}
        s2["latestActivity"] = (f"#{cur['index']} {cur['industry']} — turn {cur['turn']}"
                                if active else
                                f"{len(interviews)} interview(s) finished")
        s2["logs"] = s2["logs"][-40:]

    # 3 — judge
    if judges:
        s3["status"] = "completed" if all(j.get("done") for j in judges) else "running"
        s3["model"] = _model_name(judges[-1].get("model"))
        for j in judges:
            if j.get("io"):
                s3["inputTokens"] += _num(j["io"]["in_tok"])
                s3["outputTokens"] += _num(j["io"]["out_tok"])
                s3["cost"] += _usd(j["io"]["total"])
        scored = [iv for iv in interviews if iv.get("score")]
        if s3["status"] == "completed" and scored:
            s3["latestActivity"] = ", ".join(
                f"#{iv['index']} score {iv['score']}" for iv in scored)[:80]
        elif s3["status"] == "running":
            s3["latestActivity"] = "Evaluating requirements…"
    elif s2["status"] == "running":
        s3["status"] = "queued"

    # 4 — improver / 5 — codefix (from the shared pipeline stages)
    for st, raw, run_note in ((s4, p["improve"], "Refining prompt…"),
                              (s5, p["codefix"], "Applying code fixes…")):
        status = raw["status"]
        st["status"] = {"run": "running", "done": "completed",
                        "failed": "failed"}.get(status, "pending")
        st["model"] = _model_name(raw.get("model"))
        if raw.get("io"):
            st["inputTokens"] = _num(raw["io"]["in_tok"])
            st["outputTokens"] = _num(raw["io"]["out_tok"])
            st["cost"] = _usd(raw["io"]["total"])
        st["latestActivity"] = raw.get("note") or (
            run_note if st["status"] == "running" else None)
    if s3["status"] == "completed" and s4["status"] == "pending":
        s4["status"] = "queued"
    if s4["status"] == "completed" and s5["status"] == "pending":
        s5["status"] = "queued"
    if s5["status"] == "completed":
        s5["name"] = "Completed Fixing Cycle"
        s5["latestActivity"] = s5["latestActivity"] or "Cycle completed successfully."

    return [s1, s2, s3, s4, s5]


def payload() -> dict:
    d = collect()
    stages = build_stages(d) if d["run_dir"] else []
    completed = sum(1 for s in stages if s["status"] == "completed")
    failed = any(s["status"] == "failed" for s in stages)
    running = [s for s in stages if s["status"] == "running"]
    if failed:
        overall = "failed"
    elif stages and completed == len(stages):
        overall = "completed"
    elif d["run_finished"] and not running:
        overall = "completed"
    elif running or completed:
        overall = "running"
    else:
        overall = "idle"
    now = time.time()
    started = d.get("started_at")
    end = d.get("last_activity") if overall in ("completed", "failed") else now
    return {
        "run_id": d["run_dir"], "log": d["log"],
        "stages": stages, "lanes": build_lanes(d) if stages else [],
        "overall": overall,
        "completed_stages": completed, "total_stages": len(stages) or 5,
        "current_stage": (running[0]["name"] if running else
                          ("—" if not stages or overall != "running"
                           else next((s["name"] for s in stages
                                      if s["status"] in ("queued", "pending")),
                                     stages[-1]["name"]))),
        "fix_cycle_done": bool(stages) and stages[-1]["status"] == "completed",
        "elapsed": max(0, int((end or now) - started)) if started else None,
        "started_at": started,
        "last_activity_ago": (max(0, int(now - d["last_activity"]))
                              if d.get("last_activity") else None),
        "total_cost": round(sum(s["cost"] for s in stages), 2),
        "total_in": sum(s["inputTokens"] for s in stages),
        "total_out": sum(s["outputTokens"] for s in stages),
    }


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RequirementsBot — Cycle Monitor</title>
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
  background:var(--green);margin-right:6px;vertical-align:1px}
#sb-foot div{margin:3px 0}
#sidebar.collapsed #sb-title,#sidebar.collapsed .nav-label,#sidebar.collapsed #sb-foot{display:none}

#main{flex:1;display:flex;flex-direction:column;min-width:0;overflow-y:auto}

/* ---------- run header ---------- */
#run-header{display:flex;align-items:center;gap:14px;padding:12px 20px;
  border-bottom:1px solid var(--border);background:var(--panel);position:sticky;top:0;z-index:5}
#run-title{font-size:14px;font-weight:600}
#run-id{font:12px var(--mono);color:var(--text2)}
#run-meta{color:var(--muted);font-size:12px}
#run-actions{margin-left:auto;display:flex;gap:6px}
.act{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer}
.act:hover{border-color:var(--border-hi);color:var(--text)}

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
.badge.pending,.badge.queued,.badge.idle{color:var(--muted)}
.badge.pending .b-dot,.badge.queued .b-dot,.badge.idle .b-dot{background:var(--muted)}
.badge.retrying{color:var(--amber);border-color:#5c4a1e}
.badge.retrying .b-dot{background:var(--amber)}
.badge.completed_failures{color:var(--amber);border-color:#5c4a1e;background:rgba(251,191,36,.05)}
.badge.completed_failures .b-dot{background:var(--amber)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ---------- summary toolbar ---------- */
#summary{display:grid;width:auto;margin:14px 20px 0;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;
  grid-template-columns:minmax(95px,.85fr) minmax(110px,1fr) minmax(110px,1fr)
    minmax(105px,.95fr) minmax(130px,1.15fr) minmax(110px,1fr)
    minmax(105px,1fr) minmax(125px,1.1fr)}
.sum{padding:8px 14px;min-width:0;position:relative;
  display:flex;flex-direction:column;justify-content:center}
.sum::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.sum:last-child::after{display:none}
.sum .k{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sum .v{font:500 14px var(--mono);color:var(--text);margin-top:1px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
#s-running.hot{color:var(--accent)}
#s-completed-c{color:var(--green)}
#s-failed.hot{color:var(--red)}
@media (max-width:1180px){
  #summary{grid-template-columns:repeat(4,1fr)}
  .sum:nth-child(4)::after{display:none}
  .sum:nth-child(-n+4){border-bottom:1px solid var(--border)}
}

/* ---------- pipeline ---------- */
#improve-grid{display:flex;gap:14px;margin:14px 20px 0;flex-wrap:wrap}
.imp-tile{width:190px;aspect-ratio:1;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;cursor:pointer;color:var(--text);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:10px;padding:14px;text-align:center;transition:border-color .15s,background .15s}
.imp-tile:hover{border-color:var(--border-hi);background:var(--panel2)}
.imp-tile.on{border-color:var(--accent)}
.imp-tile .bx-ico{width:38px;height:38px;border:1px solid var(--border-hi);
  border-radius:8px;display:grid;place-items:center;font-size:18px;color:var(--accent)}
.imp-tile .bx-t{font:600 12.5px var(--sans);line-height:1.35}
.imp-tile .bx-d{font:10px var(--mono);color:var(--muted);line-height:1.4}
.imp-panel.closed{display:none}
.imp-panel.plc{margin:14px 20px 0;background:var(--panel);border:1px solid var(--border);
  border-radius:6px}
.empty{padding:40px 24px;text-align:center;color:var(--muted);font-size:12.5px;
  line-height:1.6;max-width:640px;margin:0 auto}
body.view-logs #improve-grid,body.view-chat #improve-grid,
body.view-logs .imp-panel,body.view-chat .imp-panel{display:none}
#pipeline-wrap{padding:14px 20px 0;overflow-x:auto}
#pipeline{display:flex;align-items:stretch;min-width:940px}
.stage{flex:1;min-width:172px;background:var(--panel);border:1px solid var(--border);
  border-radius:6px;padding:11px 12px;cursor:pointer;transition:border-color .2s,background .2s;
  display:flex;flex-direction:column;gap:7px}
.stage:hover{border-color:var(--border-hi)}
.stage.running{border-color:#3b5b8a;background:#161a20}
.stage.completed{border-color:#2a3a30}
.stage.failed{border-color:#553030}
.stage.pending,.stage.queued{opacity:.62}
.stage.selected{border-color:var(--accent)}
.st-top{display:flex;align-items:center;gap:8px}
.st-num{font:600 10px var(--mono);color:var(--muted);border:1px solid var(--border);
  border-radius:4px;width:18px;height:18px;display:grid;place-items:center;flex:none}
.st-ico{font-size:13px;flex:none;color:var(--text2)}
.st-name{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.st-desc{color:var(--muted);font-size:11px;margin-top:-4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.st-status{display:flex;align-items:center;gap:8px;font-size:11px}
.st-status .turn{font:11px var(--mono);color:var(--text2)}
.spin{display:inline-block;width:10px;height:10px;border:1.5px solid var(--border-hi);
  border-top-color:var(--accent);border-radius:50%;animation:rot .8s linear infinite;flex:none}
@keyframes rot{to{transform:rotate(360deg)}}
.st-bar{height:2px;background:var(--border);border-radius:1px;overflow:hidden}
.st-bar i{display:block;height:100%;width:100%}
.stage.completed .st-bar i{background:var(--green);opacity:.55}
.stage.failed .st-bar i{background:var(--red);opacity:.6}
.stage.running .st-bar i{background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:flow 1.4s linear infinite}
.stage.pending .st-bar i,.stage.queued .st-bar i{background:transparent}
@keyframes flow{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
.st-metrics{display:flex;flex-direction:column;gap:2px;font:11px var(--mono)}
.st-metrics span{color:var(--muted);font-size:9.5px;text-transform:uppercase;
  letter-spacing:.04em;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.st-metrics b{color:var(--text);font-weight:500;font-size:11.5px}
.st-model{font:10.5px var(--mono);color:var(--text2);border-top:1px solid var(--border);
  padding-top:6px;line-height:1.6}
.st-model .lbl{color:var(--muted)}

#lanes{flex:2;display:flex;flex-direction:column;gap:18px;min-width:0}
.lane-bar{display:grid;align-items:stretch;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;margin-bottom:8px;
  min-height:44px;
  grid-template-columns:minmax(260px,1.5fr) minmax(70px,.45fr) minmax(135px,.85fr)
    minmax(80px,.5fr) minmax(250px,1.6fr) minmax(105px,.65fr)
    minmax(110px,.7fr) 28px}
.lane-bar.failed{border-color:#553030}
.lane-name{display:flex;align-items:baseline;gap:10px;padding:4px 14px;min-width:0;
  position:relative}
.lane-name::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.lane-name .n{font:600 12px var(--sans);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-width:0}
.lane-name .s{font:10px var(--mono);color:var(--muted);white-space:nowrap;flex:none}
.lb-item{display:flex;flex-direction:column;justify-content:center;min-width:0;
  padding:4px 12px;position:relative}
.lb-item::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.lb-item:last-of-type::after{display:none}
.lb-item .k{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lb-item .v{font:500 11.5px var(--mono);color:var(--text);white-space:nowrap;margin-top:1px;
  overflow:hidden;text-overflow:ellipsis}
.lb-item .v.err{color:var(--red)}
.lb-more{background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:14px;padding:0;align-self:center;justify-self:center}
.lb-more:hover{color:var(--text)}
@media (max-width:1240px){
  .lane-bar{grid-template-columns:minmax(220px,1.4fr) minmax(70px,.45fr)
    minmax(110px,.75fr) minmax(75px,.5fr) minmax(200px,1.25fr)
    minmax(100px,.65fr) 28px}
  .lb-item.last-act{display:none}
}
.lane-row{display:flex;align-items:stretch}
.lane-row .stage{min-width:150px}
.connector{flex:none;width:26px;display:flex;align-items:center;position:relative}
.connector::before{content:'';height:1px;width:100%;background:var(--border)}
.connector.completed::before{background:#2f5c40}
.connector.running::before{background:linear-gradient(90deg,#2f5c40,var(--accent))}
.connector.running::after{content:'';position:absolute;width:4px;height:4px;border-radius:50%;
  background:var(--accent);top:50%;margin-top:-2px;animation:travel 1.2s linear infinite}
@keyframes travel{from{left:0;opacity:0}20%{opacity:1}80%{opacity:1}to{left:calc(100% - 4px);opacity:0}}

/* ---------- console ---------- */
#console{margin:14px 20px 20px;background:#101010;border:1px solid var(--border);
  border-radius:6px;display:flex;flex-direction:column;min-height:220px;flex:1}
#console.fullscreen{position:fixed;inset:12px;z-index:50;margin:0}
#con-bar{display:flex;align-items:center;gap:6px;padding:7px 10px;
  border-bottom:1px solid var(--border);flex-wrap:wrap}
#con-title{font:600 11px var(--sans);text-transform:uppercase;letter-spacing:.07em;
  color:var(--text2);margin-right:6px}
.chip{background:none;border:1px solid var(--border);color:var(--muted);border-radius:99px;
  padding:2px 9px;font-size:11px;cursor:pointer}
.chip:hover{color:var(--text2);border-color:var(--border-hi)}
.chip.on{color:var(--text);border-color:var(--border-hi);background:var(--panel2)}
#con-search{margin-left:auto;background:var(--panel2);border:1px solid var(--border);
  color:var(--text);border-radius:5px;padding:3px 9px;font:11.5px var(--mono);width:150px}
#con-search:focus{outline:none;border-color:var(--border-hi)}
.con-btn{background:none;border:1px solid var(--border);color:var(--muted);border-radius:5px;
  padding:3px 8px;font-size:11px;cursor:pointer}
.con-btn:hover{color:var(--text);border-color:var(--border-hi)}
.con-btn.on{color:var(--accent);border-color:#28405f}
#con-body{flex:1;overflow-y:auto;padding:8px 12px;font:11.5px/1.65 var(--mono);min-height:120px}
.ll{white-space:pre-wrap;word-break:break-all}
.ll .tag{display:inline-block;min-width:74px;color:var(--muted)}
.ll.persona .tag{color:#c084fc}.ll.interview .tag{color:var(--accent)}
.ll.judge .tag{color:var(--amber)}.ll.improve .tag{color:#67e8f9}
.ll.code .tag{color:var(--green)}.ll.system .tag{color:var(--muted)}
.ll.error{color:var(--red)}.ll.error .tag{color:var(--red)}
.ll .body{color:#c7cbd1}

/* ---------- inspector ---------- */
#inspector{position:fixed;top:0;right:-380px;width:360px;height:100vh;background:var(--panel);
  border-left:1px solid var(--border);z-index:40;transition:right .18s ease;
  display:flex;flex-direction:column}
#inspector.open{right:0;box-shadow:-18px 0 40px rgba(0,0,0,.45)}
#insp-head{display:flex;align-items:center;gap:10px;padding:14px 16px;
  border-bottom:1px solid var(--border)}
#insp-title{font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.05em}
#insp-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:15px}
#insp-close:hover{color:var(--text)}
#insp-body{flex:1;overflow-y:auto;padding:14px 16px}
.insp-sec{margin-bottom:16px}
.insp-sec h4{margin:0 0 7px;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:600}
.kv{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}
.kv .k{color:var(--text2)}.kv .v{font-family:var(--mono);font-size:11.5px}
#insp-logs{background:#101010;border:1px solid var(--border);border-radius:5px;
  padding:8px 10px;font:11px/1.6 var(--mono);color:#c7cbd1;max-height:240px;
  overflow-y:auto;white-space:pre-wrap;word-break:break-all}

#empty{padding:60px 20px;text-align:center;color:var(--muted)}

/* ---------- views: Cycles (pipeline) / Live Logs (console) / Chat ---------- */
body.view-cycles #console{display:none}
body.view-logs #summary,body.view-logs #pipeline-wrap{display:none}
body.view-logs #console{flex:1}
#chat{display:none}
body.view-chat #summary,body.view-chat #pipeline-wrap,body.view-chat #console{display:none}
body.view-chat #chat{display:flex}

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
@media (max-width:760px){
  #sidebar{display:none}
  #pipeline{flex-direction:column;min-width:0}
  .connector{width:auto;height:20px;justify-content:center;margin-left:20px}
  .connector::before{width:1px;height:100%}
}
</style></head><body class="view-cycles">
<div id="shell">
  <aside id="sidebar">
    <div id="sb-head"><div id="sb-logo">R</div><span id="sb-title">RequirementsBot</span>
      <button id="sb-toggle" title="Collapse">⟨⟩</button></div>
    <nav id="sb-nav">
      <div class="nav-item" id="nav-chat" data-view="chat"><span class="nav-ico">▶</span><span class="nav-label">Requirement Bot Chat</span></div>
      <div class="nav-item" id="nav-logs" data-view="logs"><span class="nav-ico">≣</span><span class="nav-label">Live Logs</span></div>
      <div class="nav-item active" id="nav-cycles" data-view="cycles"><span class="nav-ico">◈</span><span class="nav-label">Learning &amp; Training Cycles</span></div>
      <div class="nav-item"><span class="nav-ico">◉</span><span class="nav-label">Personas</span></div>
      <div class="nav-item"><span class="nav-ico">✎</span><span class="nav-label">Interviews</span></div>
      <div class="nav-item"><span class="nav-ico">⚖</span><span class="nav-label">Evaluations</span></div>
      <div class="nav-item"><span class="nav-ico">‹›</span><span class="nav-label">Fixes</span></div>
      <div class="nav-item"><span class="nav-ico">≡</span><span class="nav-label">Reports</span></div>
      <div class="nav-item"><span class="nav-ico">⚙</span><span class="nav-label">Settings</span></div>
    </nav>
    <div id="sb-foot">
      <div><span class="dot"></span>API connected</div>
      <div><span class="dot"></span>Claude operational</div>
      <div style="color:#52525b">v1.0.0</div>
    </div>
  </aside>

  <div id="main">
    <div id="run-header">
      <div>
        <div id="run-title">Cycle / Run Detail</div>
        <span id="run-id">—</span> <span id="run-meta"></span>
      </div>
      <span class="badge idle" id="run-badge"><span class="b-dot"></span><span id="run-badge-txt">Idle</span></span>
      <div id="run-actions">
        <button class="act" title="Pause">⏸</button>
        <button class="act" title="Stop">■</button>
        <button class="act" title="Restart">↻</button>
        <button class="act" title="Settings">⚙</button>
        <button class="act" title="More">⋯</button>
      </div>
    </div>

    <div id="summary">
      <div class="sum"><div class="k">Total Cost</div><div class="v" id="s-cost">$0.00</div></div>
      <div class="sum"><div class="k">Input Tokens</div><div class="v" id="s-in">0</div></div>
      <div class="sum"><div class="k">Output Tokens</div><div class="v" id="s-out">0</div></div>
      <div class="sum"><div class="k">Elapsed Time</div><div class="v" id="s-elapsed">—</div></div>
      <div class="sum"><div class="k">Run State</div>
        <div class="v" style="margin-top:1px"><span class="badge idle" id="s-state"><span class="b-dot"></span><span id="s-state-txt">Idle</span></span></div></div>
      <div class="sum"><div class="k">Cycles Running</div><div class="v" id="s-running">0</div></div>
      <div class="sum"><div class="k">Failed Cycles</div><div class="v" id="s-failed">0</div></div>
      <div class="sum"><div class="k">Completed Cycles</div><div class="v" id="s-completed-c">0</div></div>
    </div>

    <div id="improve-grid">
      <button class="imp-tile" data-panel="pipeline-wrap">
        <span class="bx-ico">⟳</span>
        <span class="bx-t">Requirement Bot Improvement Cycle</span>
        <span class="bx-d">persona interviews, judging, prompt improvement and code fixes</span>
      </button>
      <button class="imp-tile" data-panel="builder-wrap">
        <span class="bx-ico">🛠</span>
        <span class="bx-t">Builder Bot Improvement Cycle</span>
        <span class="bx-d">builds the chatbot from the brief, then improves itself</span>
      </button>
      <button class="imp-tile" data-panel="brief-wrap">
        <span class="bx-ico">📋</span>
        <span class="bx-t">Brief Quality Cycle</span>
        <span class="bx-d">a builder persona tries to build from each brief and reports what is ambiguous or missing</span>
      </button>
      <button class="imp-tile" data-panel="endcust-wrap">
        <span class="bx-ico">🎭</span>
        <span class="bx-t">End-Customer Simulation Cycle</span>
        <span class="bx-d">personas play customers of the built chatbot; a judge scores how it handled them</span>
      </button>
      <button class="imp-tile" data-panel="feedback-wrap">
        <span class="bx-ico">📥</span>
        <span class="bx-t">Live Feedback Cycle</span>
        <span class="bx-d">replays real conversation transcripts as test cases; real failures beat synthetic ones</span>
      </button>
      <button class="imp-tile" data-panel="regression-wrap">
        <span class="bx-ico">🛡</span>
        <span class="bx-t">Regression Cycle</span>
        <span class="bx-d">re-runs golden interviews after every change and diffs scores, so improvements never quietly break things</span>
      </button>
    </div>
    <div id="pipeline-wrap" class="closed imp-panel"><div id="pipeline"><div id="empty">Waiting for a run to appear in parallel_runs/ …</div></div></div>
    <div id="builder-wrap" class="closed imp-panel plc"><div class="empty">No Builder Bot runs yet — this cycle is not built yet. It will build the chatbot from each brief, judge the result against the brief, then improve its own prompt and code.</div></div>
    <div id="brief-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. This cycle scores the BRIEF itself, not the interview: a builder persona tries to build from it and files everything ambiguous, missing, or contradictory back to the improver.</div></div>
    <div id="endcust-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. The ultimate test: personas play customers of the BUILT chatbot (bookings, complaints, discounts) and a judge scores whether it handled them like the brief promised.</div></div>
    <div id="feedback-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. Same machinery as the persona runs, but the test cases are REAL transcripts and uploaded chat exports instead of generated personas.</div></div>
    <div id="regression-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. Cheapest and most protective: a fixed set of golden interviews re-runs after every prompt or code change, and score diffs catch an "improvement" that quietly makes things worse.</div></div>

    <div id="console">
      <div id="con-bar">
        <span id="con-title">Live Cycle Logs</span>
        <button class="chip on" data-f="all">All</button>
        <button class="chip" data-f="persona">Persona</button>
        <button class="chip" data-f="interview">Interview</button>
        <button class="chip" data-f="judge">Judge</button>
        <button class="chip" data-f="improve">Improver</button>
        <button class="chip" data-f="code">Fixing Code</button>
        <button class="chip" data-f="system">System</button>
        <button class="chip" data-f="error">Errors</button>
        <input id="con-search" placeholder="filter…" spellcheck="false">
        <button class="con-btn on" id="con-scroll" title="Auto-scroll">⇣ auto</button>
        <button class="con-btn" id="con-pause" title="Pause updates">⏸</button>
        <button class="con-btn" id="con-copy" title="Copy logs">⧉</button>
        <button class="con-btn" id="con-clear" title="Clear view">✕</button>
        <button class="con-btn" id="con-full" title="Fullscreen">⛶</button>
      </div>
      <div id="con-body"></div>
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

  <div id="inspector">
    <div id="insp-head"><span id="insp-title">Stage</span>
      <span class="badge idle" id="insp-badge"><span class="b-dot"></span><span id="insp-badge-txt"></span></span>
      <button id="insp-close">✕</button></div>
    <div id="insp-body"></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const STAGE_ICONS = {persona:'◉', interview:'✎', judge:'⚖', improve:'⟳', codefix:'‹›'};
const STATUS_TXT = {pending:'Pending', queued:'Queued', running:'Running',
                    completed:'Completed', failed:'Failed', retrying:'Retrying', idle:'Idle',
                    completed_failures:'Completed With Failures'};
const STATUS_DOT = {pending:'○', queued:'○', running:'●', completed:'✓', failed:'✕'};
let state = {stages:[], lanes:[], selected:null, paused:false, autoscroll:true,
             filter:'all', search:'', fullscreen:false, cleared:0};

function fmtTok(n){
  if(!n) return '0';
  if(n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if(n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return String(n);
}
function fmtElapsed(s){
  if(s == null) return '—';
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), sec = s%60;
  return (h ? h+'h ' : '') + (h||m ? m+'m ' : '') + sec+'s';
}
function badge(el, status){
  el.parentElement ? null : 0;
  el.className = 'badge ' + status;
}

/* ---------- stage cards ---------- */
function stageCard(s){
  const running = s.status === 'running';
  const statusRow = running
    ? `<span class="spin"></span><span style="color:var(--accent);font-weight:600">Running</span>` +
      (s.progress ? `<span class="turn">${s.progress.label} ${s.progress.value}</span>` : '')
    : `<span style="color:${s.status==='completed'?'var(--green)':s.status==='failed'?'var(--red)':'var(--muted)'}">
       ${STATUS_DOT[s.status]||'○'} ${STATUS_TXT[s.status]||s.status}</span>`;
  return `<div class="stage ${s.status}${state.selected===s.id?' selected':''}" data-id="${s.id}">
    <div class="st-top"><span class="st-num">${s.number}</span>
      <span class="st-ico">${STAGE_ICONS[s.id.split('-')[0]]||'▣'}</span>
      <span class="st-name">${s.name}</span></div>
    <div class="st-desc">${s.description}</div>
    <div class="st-status">${statusRow}</div>
    <div class="st-bar"><i></i></div>
    <div class="st-metrics">
      <span>Cost<b>$${s.cost.toFixed(2)}</b></span>
      <span>Input Tokens<b>${fmtTok(s.inputTokens)}</b></span>
      <span>Output Tokens<b>${fmtTok(s.outputTokens)}</b></span>
    </div>
    <div class="st-model">
      <span class="lbl">model</span> ${s.model||'—'}<br>
      <span class="lbl">usage</span> ${s.usage}
    </div>
  </div>`;
}
function connector(prev, next){
  let cls = 'pending';
  if(prev.status === 'completed') cls = (next.status === 'running') ? 'running' : 'completed';
  else if(prev.status === 'running') cls = 'running';
  return `<div class="connector ${cls}"></div>`;
}
function allStages(){
  const lane = state.lanes.flatMap(l =>
    [l.persona, l.interview, l.judge, l.improve, l.codefix]);
  return state.stages.concat(lane);
}
function renderPipeline(){
  const p = $('#pipeline');
  if(!state.stages.length){ return; }
  const [s1, s2, s3, s4, s5] = state.stages;
  let html;
  if(state.lanes.length){
    html = `<div id="lanes">` + state.lanes.map(l => {
      const seq = [l.persona, l.interview, l.judge, l.improve, l.codefix];
      const cost = seq.reduce((a,s) => a + s.cost, 0);
      const tin = seq.reduce((a,s) => a + s.inputTokens, 0);
      const tout = seq.reduce((a,s) => a + s.outputTokens, 0);
      const done = seq.filter(s => s.status === 'completed').length;
      const running = seq.find(s => s.status === 'running');
      const failedStage = seq.find(s => s.status === 'failed');
      const st = running ? 'running'
               : failedStage ? 'failed'
               : done === seq.length ? 'completed'
               : seq.some(s => s.status === 'queued') ? 'queued' : 'pending';
      // "Step 4 / 5 · Fixing Code" — the stage being worked on (or where it stopped)
      const cur = running || failedStage
                || (done === seq.length ? seq[seq.length-1] : seq[Math.min(done, seq.length-1)]);
      const stepNo = seq.indexOf(cur) + 1;
      const ago = state.lastActivityAgo;
      const lastAct = ago == null ? '—'
        : (st === 'failed' ? 'Failed ' : '') + fmtElapsed(ago) + ' ago';
      return `<div class="lane">
        <div class="lane-bar${st === 'failed' ? ' failed' : ''}">
          <div class="lane-name"><span class="n">#${l.index} ${l.industry}</span>
            ${l.score ? `<span class="s">score ${l.score}</span>` : ''}</div>
          <div class="lb-item"><span class="k">Cost</span><span class="v">$${cost.toFixed(2)}</span></div>
          <div class="lb-item"><span class="k">Tokens</span>
            <span class="v" title="Input ${tin.toLocaleString()}\nOutput ${tout.toLocaleString()}">${fmtTok(tin)} / ${fmtTok(tout)}</span></div>
          <div class="lb-item"><span class="k">Elapsed</span><span class="v">${fmtElapsed(state.elapsed)}</span></div>
          <div class="lb-item"><span class="k">Step</span>
            <span class="v">Step ${stepNo} / ${seq.length} · ${cur.name}</span></div>
          <div class="lb-item"><span class="k">State</span>
            <span class="v" style="margin-top:2px"><span class="badge ${st}"><span class="b-dot"></span>${STATUS_TXT[st]}</span></span></div>
          <div class="lb-item last-act"><span class="k">Last Activity</span>
            <span class="v${st === 'failed' ? ' err' : ''}">${lastAct}</span></div>
          <button class="lb-more" title="More">⋯</button>
        </div>
        <div class="lane-row">${seq.map((s,i) =>
          (i ? connector(seq[i-1], s) : '') + stageCard(s)).join('')}</div>
      </div>`;
    }).join('') + `</div>`;
  }else{
    html = stageCard(s1) + connector(s1, s2) + stageCard(s2)
         + connector(s2, s3) + stageCard(s3) + connector(s3, s4)
         + stageCard(s4) + connector(s4, s5) + stageCard(s5);
  }
  p.innerHTML = html;
  p.querySelectorAll('.stage').forEach(el =>
    el.onclick = () => openInspector(el.dataset.id));
}

/* ---------- inspector ---------- */
function openInspector(id){
  state.selected = id; renderPipeline();
  const s = allStages().find(x => x.id === id); if(!s) return;
  $('#insp-title').textContent = s.name;
  $('#insp-badge').className = 'badge ' + s.status;
  $('#insp-badge-txt').textContent = STATUS_TXT[s.status] || s.status;
  const kv = (k,v) => `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $('#insp-body').innerHTML = `
    <div class="insp-sec"><h4>Overview</h4>
      ${kv('Status', STATUS_TXT[s.status]||s.status)}
      ${kv('Model', s.model||'—')}
      ${kv('Usage', s.usage)}
      ${s.progress ? kv('Progress', s.progress.label+' '+s.progress.value) : ''}
    </div>
    <div class="insp-sec"><h4>Tokens</h4>
      ${kv('Input', s.inputTokens.toLocaleString())}
      ${kv('Output', s.outputTokens.toLocaleString())}
    </div>
    <div class="insp-sec"><h4>Cost</h4>
      ${kv('Total', '$'+s.cost.toFixed(2))}
      ${s.id.startsWith('interview') ? (
        s.botCost != null
          ? kv('RequirementsBot', '$'+s.botCost) + kv('Persona', '$'+s.personaCost)
          : kv('RequirementsBot / Persona split', 'available when interview ends')
      ) : ''}
    </div>
    <div class="insp-sec"><h4>Latest activity</h4>
      <div style="font-size:12px;color:var(--text2)">${s.latestActivity||'—'}</div></div>
    <div class="insp-sec"><h4>Logs</h4>
      <div id="insp-logs">${(s.logs&&s.logs.length)?s.logs.join('\n'):'(no stage logs)'}</div></div>`;
  $('#inspector').classList.add('open');
}
$('#insp-close').onclick = () => {
  $('#inspector').classList.remove('open'); state.selected = null; renderPipeline();
};

/* ---------- console ---------- */
function classify(line){
  const t = line.trim();
  if(/failed|error|FAILED|exited/i.test(t) && !/0 failed/.test(t)) return 'error';
  if(t.startsWith('[personas]')) return 'persona';
  if(t.startsWith('[improve]')) return 'improve';
  if(t.startsWith('[codefix]')) return 'code';
  if(/^\[\d{3} .*judg/i.test(t) || /judge\[/.test(t)) return 'judge';
  if(/^\[\d{3} /.test(t)) return 'interview';
  return 'system';
}
const TAG_LABEL = {persona:'persona', interview:'interview', judge:'judge',
                   improve:'improver', code:'code', system:'system', error:'error'};
function renderLog(text){
  if(state.paused) return;
  const lines = text.split('\n').slice(state.cleared);
  const q = state.search.toLowerCase();
  const body = $('#con-body');
  const stick = state.autoscroll;
  body.innerHTML = lines.filter(l => l.trim()).map(l => {
    const c = classify(l);
    if(state.filter !== 'all' && c !== state.filter &&
       !(state.filter === 'error' && c === 'error')) return '';
    if(q && !l.toLowerCase().includes(q)) return '';
    return `<div class="ll ${c}"><span class="tag">${TAG_LABEL[c]}</span><span class="body">${
      l.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span></div>`;
  }).join('');
  if(stick) body.scrollTop = body.scrollHeight;
}
document.querySelectorAll('.chip').forEach(ch => ch.onclick = () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
  ch.classList.add('on'); state.filter = ch.dataset.f; renderLog(lastLog);
});
$('#con-search').oninput = e => { state.search = e.target.value; renderLog(lastLog); };
$('#con-scroll').onclick = e => {
  state.autoscroll = !state.autoscroll;
  e.target.classList.toggle('on', state.autoscroll);
};
$('#con-pause').onclick = e => {
  state.paused = !state.paused;
  e.target.classList.toggle('on', state.paused);
  if(!state.paused) renderLog(lastLog);
};
$('#con-copy').onclick = () => navigator.clipboard.writeText(lastLog).catch(()=>{});
$('#con-clear').onclick = () => { state.cleared = lastLog.split('\n').length; renderLog(lastLog); };
$('#con-full').onclick = () => $('#console').classList.toggle('fullscreen');
$('#sb-toggle').onclick = () => $('#sidebar').classList.toggle('collapsed');
document.querySelectorAll('.imp-tile').forEach(tile => tile.onclick = () => {
  const panel = document.getElementById(tile.dataset.panel);
  const opening = panel.classList.contains('closed');
  // one cycle open at a time
  document.querySelectorAll('.imp-panel').forEach(p => p.classList.add('closed'));
  document.querySelectorAll('.imp-tile').forEach(t => t.classList.remove('on'));
  if(opening){ panel.classList.remove('closed'); tile.classList.add('on'); }
});
document.querySelectorAll('.nav-item[data-view]').forEach(item => item.onclick = () => {
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  item.classList.add('active');
  document.body.className = 'view-' + item.dataset.view;
  if(item.dataset.view === 'logs') renderLog(lastLog);
  if(item.dataset.view === 'chat' && !chat.loaded) loadChat();
});

/* ---------- Requirement Bot chat ---------- */
const chat = {loaded:false, busy:false, complete:false};
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
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
  try{ renderChat(await (await fetch('/chat/history')).json()); }
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
      body: JSON.stringify({message: text})})).json();
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
      body: JSON.stringify({files})})).json();
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
  const d = await (await fetch('/chat/reset', {method:'POST'})).json();
  chat.complete = false; renderChat(d); $('#chat-input').disabled = false;
};

/* ---------- polling ---------- */
let lastLog = '';
async function tick(){
  let d;
  try{ d = await (await fetch('/data')).json(); }catch(e){ return; }
  state.stages = d.stages || [];
  state.lanes = d.lanes || [];
  $('#run-id').textContent = d.run_id ? 'Run #' + d.run_id : 'no runs yet';
  $('#run-meta').textContent = d.started_at
    ? '· started ' + fmtElapsed(Math.floor(Date.now()/1000 - d.started_at)) + ' ago' : '';
  state.elapsed = d.elapsed;
  state.lastActivityAgo = d.last_activity_ago;

  // cycle counters, derived from each lane's stage states
  const laneSt = state.lanes.map(l => {
    const seq = [l.persona, l.interview, l.judge, l.improve, l.codefix];
    if(seq.some(s => s.status === 'running')) return 'running';
    if(seq.some(s => s.status === 'failed')) return 'failed';
    if(l.codefix.status === 'completed') return 'completed';
    return 'queued';
  });
  const nRun = laneSt.filter(s => s === 'running').length;
  const nFail = laneSt.filter(s => s === 'failed').length;
  const nDone = laneSt.filter(s => s === 'completed').length;

  let overall;
  if(!state.lanes.length) overall = d.overall;
  else if(nRun > 0 || d.overall === 'running') overall = 'running';
  else if(nFail > 0) overall = nDone > 0 ? 'completed_failures' : 'failed';
  else overall = d.overall === 'idle' ? 'idle' : 'completed';
  const overallTxt = STATUS_TXT[overall] || overall;
  $('#run-badge').className = 'badge ' + overall;
  $('#run-badge-txt').textContent = overallTxt;
  $('#s-state').className = 'badge ' + overall;
  $('#s-state-txt').textContent = overallTxt;

  $('#s-cost').textContent = '$' + (d.total_cost||0).toFixed(2);
  $('#s-in').textContent = fmtTok(d.total_in||0);
  $('#s-out').textContent = fmtTok(d.total_out||0);
  $('#s-elapsed').textContent = fmtElapsed(d.elapsed);
  $('#s-running').textContent = nRun;
  $('#s-running').classList.toggle('hot', nRun > 0);
  $('#s-failed').textContent = nFail;
  $('#s-failed').classList.toggle('hot', nFail > 0);
  $('#s-completed-c').textContent = nDone;
  renderPipeline();
  if(state.selected) {
    const wasOpen = $('#inspector').classList.contains('open');
    if(wasOpen) openInspector(state.selected);
  }
  lastLog = d.log || '';
  renderLog(lastLog);
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


# ---------- live chat with RequirementsBot (the same bot main.py runs) ----------
CHAT = {"bot": None, "turn": None, "busy": False, "shown": []}
CHAT_LOCK = threading.Lock()


def _chat_bot():
    """Lazy: importing requirements_bot pulls langchain — only pay for it
    when the chat page is actually used."""
    if CHAT["bot"] is None:
        from requirements_bot import RequirementsBot
        bot = RequirementsBot()
        bot.uploads_dir.mkdir(exist_ok=True)
        CHAT["bot"] = bot
    return CHAT["bot"]


def _save_brief(partial: bool) -> None:
    bot, turn = CHAT["bot"], CHAT["turn"]
    if bot is None or turn is None:
        return
    brief = bot.brief(turn)
    if partial:
        brief["partial"] = True
    (ROOT / "requirements_brief.json").write_text(
        json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")


def chat_history() -> dict:
    # bot.chat_history holds the model's raw JSON turns; CHAT["shown"] keeps
    # the clean conversational texts that send() returns (what main.py prints)
    if not CHAT["shown"]:
        from requirements_bot import RequirementsBot
        CHAT["shown"] = [{"role": "ai", "text": RequirementsBot.GREETING}]
    complete = CHAT["bot"].complete if CHAT["bot"] else False
    return {"messages": CHAT["shown"], "complete": complete,
            "busy": CHAT["busy"]}


def chat_send(message: str) -> dict:
    with CHAT_LOCK:
        if CHAT["busy"]:
            return {"error": "The bot is still answering — wait a moment."}
        CHAT["busy"] = True
    try:
        bot = _chat_bot()
        chat_history()  # ensure the greeting is seeded before appending
        CHAT["shown"].append({"role": "human", "text": message})
        msgs, turn = bot.send(message)
        CHAT["shown"] += [{"role": "ai", "text": m} for m in msgs]
        CHAT["turn"] = turn
        if bot.complete:
            _save_brief(partial=False)
        return chat_history() | {
            "saved": "requirements_brief.json" if bot.complete else None}
    except RuntimeError as e:
        # materials gate: the bot refuses to interview blind over unreadable
        # files — surface it in the thread instead of a 500
        return chat_history() | {"error": f"Stopped — {e}. "
                                 "Fix or remove the files in uploads/."}
    except Exception as e:
        return chat_history() | {"error": f"{type(e).__name__}: {e}"}
    finally:
        CHAT["busy"] = False


ALLOWED_EXTS = {".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def chat_upload(files: list) -> dict:
    """Save attached files into uploads/ so the bot's material scan sees them."""
    import base64
    updir = ROOT / "uploads"
    updir.mkdir(exist_ok=True)
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
    return {"saved": saved, "rejected": rejected}


def chat_reset() -> dict:
    with CHAT_LOCK:
        if CHAT["busy"]:
            return {"error": "The bot is still answering — wait a moment."}
        if CHAT["bot"] is not None and not CHAT["bot"].complete:
            _save_brief(partial=True)  # don't lose a half-finished interview
        CHAT.update(bot=None, turn=None, shown=[])
    return chat_history()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            body = json.dumps(payload()).encode("utf-8")
            ctype = "application/json"
        elif self.path == "/chat/history":
            body = json.dumps(chat_history()).encode("utf-8")
            ctype = "application/json"
        else:
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}
        if self.path == "/chat/send":
            out = chat_send(str(req.get("message", "")).strip())
        elif self.path == "/chat/upload":
            out = chat_upload(req.get("files") or [])
        elif self.path == "/chat/reset":
            out = chat_reset()
        else:
            out = {"error": "unknown endpoint"}
        body = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console quiet
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
