# controller.py — the deterministic execution controller.
#
# The ONLY code that transitions project state, commits spec revisions, and
# records approvals. RequirementsBot proposes (via its InterviewTurn); the AI
# supervisor advises (orchestrator/supervisor.py); humans decide; this module
# executes and persists.
#
# Interview completion, readiness, and approval are three different facts:
#   interview_closed  — the bot's own gates ran and it signed off
#   review_required   — the readiness rubric named blocking gaps for a human
#   approved          — an operator explicitly approved an exact revision
import json
import threading
import time
from pathlib import Path

from orchestrator import store

ROOT = Path(__file__).parent.parent
UPLOADS_ROOT = store.DATA_DIR / "uploads"

# Per-project in-process locks: one interview turn at a time per project.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_busy: dict[str, bool] = {}


def _lock(pid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(pid, threading.Lock())


def is_busy(pid: str) -> bool:
    return _busy.get(pid, False)


def active_runs() -> list[str]:
    """Projects with a model call ACTUALLY executing right now. This is worker
    activity — distinct from a lifecycle state like 'interviewing', which can
    coexist with an idle worker waiting on the person."""
    return [pid for pid, b in _busy.items() if b]


def uploads_dir(pid: str) -> Path:
    """Per-project materials directory. The default project keeps the legacy
    ROOT/uploads folder so existing behavior (and the CLI) is unchanged."""
    if pid == store.DEFAULT_PROJECT:
        return ROOT / "uploads"
    d = UPLOADS_ROOT / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_bot(pid: str):
    from requirements_bot import RequirementsBot
    state = store.load_session(pid)
    if state is None:
        bot = RequirementsBot(uploads_dir=uploads_dir(pid))
        bot.uploads_dir.mkdir(exist_ok=True)
        return bot
    return RequirementsBot.from_dict(state, uploads_dir=uploads_dir(pid))


# ---------------- the interview turn ----------------
def send_interview_message(pid: str, message: str) -> dict:
    """One owner message through the full pipeline: persist -> run bot on
    rehydrated state -> validate -> commit revision + snapshot + messages +
    events atomically-enough for one process. Returns the chat payload."""
    lk = _lock(pid)
    if not lk.acquire(blocking=False):
        return {"error": "The bot is still answering — wait a moment."}
    _busy[pid] = True
    try:
        project = store.get_project(pid)
        if project is None:
            return {"error": f"unknown project {pid}"}
        if store.load_session(pid) and _session_complete(pid):
            return {"error": "This interview is already complete. "
                             "Start a new project for another interview."}

        store.add_message(pid, "interview", "owner", message)
        store.append_event(pid, "message.received", "owner", {})
        if project["state"] in ("created",):
            store.set_project_state(pid, "interviewing")

        bot = _make_bot(pid)
        usage_before = dict(bot.usage)
        started = time.time()
        try:
            msgs, turn = bot.send(message)
        except RuntimeError as e:  # materials gate — surfaced, not a crash
            store.append_event(pid, "run.failed", "controller", {"error": str(e)})
            return {"error": f"Stopped — {e}. Fix or remove the files in the "
                             f"uploads folder."}
        except Exception as e:
            store.add_invocation(pid, "interview_turn", bot.model, None,
                                 error=f"{type(e).__name__}: {e}")
            store.append_event(pid, "run.failed", "controller",
                               {"error": f"{type(e).__name__}: {e}"})
            return {"error": f"{type(e).__name__}: {e}"}

        turn_usage = {k: bot.usage[k] - usage_before.get(k, 0) for k in bot.usage}
        store.add_invocation(pid, "interview_turn", bot.model, turn_usage,
                             duration=time.time() - started)

        for m in msgs:
            store.add_message(pid, "interview", "bot", m)
        rev = store.commit_revision(pid, turn.requirements.model_dump())
        store.save_session(pid, bot.to_dict(), bot.complete)

        saved = None
        if bot.complete:
            store.set_project_state(pid, "interview_closed")
            store.append_event(pid, "interview.closed", "controller", {"rev": rev})
            saved = _export_brief_file(pid, bot, turn)
            evaluate_readiness(pid)   # files ReviewRequests for blocking gaps
            # Review is always an explicit human step, even with zero gaps:
            # interview completion is never handoff readiness.
            store.set_project_state(pid, "review_required")
        return chat_payload(pid) | {"saved": saved}
    finally:
        _busy[pid] = False
        lk.release()


def _session_complete(pid: str) -> bool:
    s = store.load_session(pid)
    return bool(s and s.get("complete"))


def _export_brief_file(pid: str, bot, turn) -> str:
    """Preserved deliverable: the legacy brief JSON. The default project keeps
    writing the historical requirements_brief.json path."""
    brief = bot.brief(turn)
    if pid == store.DEFAULT_PROJECT:
        path = ROOT / "requirements_brief.json"
    else:
        path = store.DATA_DIR / f"brief_{pid}.json"
    path.write_text(json.dumps(brief, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    store.append_event(pid, "export.created", "controller", {"path": path.name})
    return path.name


def chat_payload(pid: str) -> dict:
    """The legacy /chat/history response shape, served from the store."""
    from requirements_bot import RequirementsBot
    msgs = store.get_messages(pid, "interview")
    shown = ([{"role": "ai", "text": RequirementsBot.GREETING}] if not msgs else
             [{"role": "human" if m["role"] == "owner" else "ai", "text": m["text"]}
              for m in msgs])
    if msgs and msgs[0]["role"] != "bot":
        shown.insert(0, {"role": "ai", "text": RequirementsBot.GREETING})
    return {"messages": shown, "complete": _session_complete(pid),
            "busy": is_busy(pid)}


def reset_interview(pid: str) -> dict:
    """New interview for a project. A half-finished one is exported first."""
    lk = _lock(pid)
    if not lk.acquire(blocking=False):
        return {"error": "The bot is still answering — wait a moment."}
    try:
        state = store.load_session(pid)
        if state and not state.get("complete"):
            # Don't lose a half-finished interview: save a partial brief first.
            reqs = store.get_revision(pid)
            if reqs:
                partial = {"requirements": reqs["requirements"],
                           "conversation_analysis": state.get("analysis"),
                           "partial": True}
                path = (ROOT / "requirements_brief.json"
                        if pid == store.DEFAULT_PROJECT
                        else store.DATA_DIR / f"brief_{pid}.json")
                path.write_text(json.dumps(partial, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        store.clear_session(pid)
        # History, revisions and events are audit records: they stay.
        store.append_event(pid, "interview.reset", "operator", {})
        store.set_project_state(pid, "created")
        return chat_payload(pid)
    finally:
        lk.release()


# ---------------- chatbot-creation flow (versioned display/workflow path) ----
# The single source of the milestone path the owner console renders. A stage
# names its responsible capability/authority; whether that capability EXISTS
# comes from the registry at read time — planned capability and per-flow
# execution state are separate facts.
FLOW_TEMPLATE = {
    "version": "chatbot-flow@2",
    "stages": [
        {"id": "interviewing", "label": "Interview",
         "who": "Requirements Bot", "capability": "requirements_bot"},
        {"id": "architecture", "label": "Architecture",
         "who": "Architect Bot", "capability": "architecture_agent"},
        {"id": "building", "label": "Building",
         "who": "Builder Bot", "capability": "builder_agent"},
        {"id": "testing_repair", "label": "Testing & Repair",
         "who": "Tester/Fixer Bot", "capability": "evaluation_agent"},
        {"id": "bot_created", "label": "Bot Created",
         "who": "Outcome", "capability": None},
    ],
}
# Requirements review/approval is no longer its own milestone: it is the
# handoff gate INTO Architecture. The gap/review/approve workflow lives in
# the flow detail's Requirements tab, and Architecture will not accept work
# until the exact revision is approved.


def flow_projection(row: dict, planned_caps: set[str],
                    is_client: bool) -> dict:
    """Deterministic read-model for one flow card. Derives stage states ONLY
    from recorded facts: lifecycle state, the in-process busy flag (actual
    executing work), review/approval records. Never from transcripts, elapsed
    time, or model text. Bot Created stays unmet until real deliverable,
    test, acceptance and finalization records exist — none do today."""
    state = row["state"]
    busy = is_busy(row["id"])
    interview_done = bool(row["interview_complete"]) or state in (
        "interview_closed", "review_required", "approved")
    review_done = bool(row["approved"])

    stages, current = [], None

    def add(sid, status, note=None):
        nonlocal current
        # A 'blocked' stage (next in line but its capability is not
        # implemented) is still where the flow currently stands.
        if status in ("current", "blocked") and current is None:
            current = sid
        stages.append({"id": sid, "status": status, "note": note})

    # 1 interviewing
    if interview_done:
        note = "Interview closed"
        if not row["interview_complete"] and state != "approved":
            note = "Interview closed · partial brief"
        add("interviewing", "completed", note)
    elif busy:
        add("interviewing", "current", "Processing answer")
    elif row["has_session"]:
        add("interviewing", "current", "Waiting for client")
    else:
        add("interviewing", "current", "Not started — no messages yet")
    # 2 architecture — its entry gate is requirements approval (the review
    # workflow lives in the flow detail's Requirements tab)
    if not interview_done:
        add("architecture", "not_started", None)
    elif not review_done:
        note = (f"Awaiting requirements approval — {row['blocking_reviews']} "
                f"blocking gap(s)" if row["blocking_reviews"]
                else "Awaiting requirements approval")
        add("architecture", "current", note)
    elif "architecture_agent" in planned_caps:
        add("architecture", "blocked",
            "Planned capability · not implemented — flow stops here")
    else:
        add("architecture", "not_started", None)   # future: real availability
    # 3-4 future specialists
    for sid, cap in (("building", "builder_agent"),
                     ("testing_repair", "evaluation_agent")):
        if cap in planned_caps:
            add(sid, "planned", "Planned · Not implemented")
        else:
            add(sid, "not_started", None)
    # 5
    add("bot_created", "unmet",
        "Requires a real deliverable, its test results, acceptance and a "
        "recorded finalization — none exist yet")

    return {
        "flow_id": row["id"], "template_version": FLOW_TEMPLATE["version"],
        "name": (row.get("business_name")
                 or (None if is_client else row["name"])
                 or "New chatbot request"),
        "contact": row.get("contact_name"),
        "is_test": not is_client,
        "state": state, "busy": busy,
        "interview_complete": bool(row["interview_complete"]),
        "head_rev": row.get("head_rev"),
        "open_reviews": row["open_reviews"],
        "blocking_reviews": row["blocking_reviews"],
        "approved": review_done,
        "last_activity_ts": row.get("last_ts") or row.get("created_ts"),
        "current_stage": current, "stages": stages,
    }


# ---------------- readiness (versioned rubric, computed in code) ----------------
RUBRIC_VERSION = "readiness-rubric@1"


def evaluate_readiness(pid: str) -> list[dict]:
    """Applicability-aware named gaps from the head revision. Blocking gaps
    become ReviewRequests. Interview completion never implies readiness."""
    head = store.get_revision(pid)
    if not head:
        return []
    gaps = _rubric_gaps(head["requirements"])
    for g in gaps:
        if g["blocking"]:
            store.add_review(pid, "readiness_gap",
                             f"[{g['name']}] {g['why']}", True, head["rev"])
    store.append_event(pid, "readiness.evaluated", "controller",
                       {"rubric": RUBRIC_VERSION,
                        "blocking": sum(1 for g in gaps if g["blocking"]),
                        "total": len(gaps), "rev": head["rev"]})
    return gaps


# ---------------- approval ----------------
def approve(pid: str, revision: int, reason: str) -> dict:
    """Explicit human approval of an exact revision. Refused unless the
    interview is closed, the revision is head, and no blocking review is open."""
    project = store.get_project(pid)
    if not project:
        return {"error": "unknown project"}
    if project["state"] not in ("interview_closed", "review_required"):
        return {"error": f"project is '{project['state']}' — approval needs a "
                         f"closed interview"}
    head = store.head_revision(pid)
    if revision != head:
        return {"error": f"stale revision: you approved rev {revision} but head "
                         f"is rev {head}. Review the newer revision."}
    blocking = [rv for rv in store.open_reviews(pid) if rv["blocking"]]
    if blocking:
        return {"error": f"{len(blocking)} blocking review(s) still open — "
                         f"resolve them first"}
    store.add_approval(pid, revision, "operator", reason)
    store.set_project_state(pid, "approved")
    return {"ok": True, "revision": revision}


# ---------------- export ----------------
def export_package(pid: str, fmt: str = "legacy") -> dict:
    head = store.get_revision(pid)
    if not head:
        return {"error": "nothing to export yet"}
    session = store.load_session(pid) or {}
    legacy = {"requirements": head["requirements"],
              "conversation_analysis": session.get("analysis")}
    if not _session_complete(pid):
        legacy["partial"] = True
    if fmt == "legacy":
        return legacy
    approval = store.active_approval(pid)
    return {
        "manifest": {"project_id": pid, "revision": head["rev"],
                     "schema_version": "botbot-brief@1",
                     "generated_ts": time.time(),
                     "approval": approval and {"actor": approval["actor"],
                                               "revision": approval["revision"],
                                               "ts": approval["ts"]}},
        "legacy_brief": legacy,
        "extended": {
            "readiness": evaluate_readiness_readonly(pid),
            "open_reviews": store.open_reviews(pid),
            "state": (store.get_project(pid) or {}).get("state"),
        },
    }


def evaluate_readiness_readonly(pid: str) -> list[dict]:
    """Same rubric, no side effects (no review filing, no events) — for
    display and export paths that must stay pure reads."""
    head = store.get_revision(pid)
    if not head:
        return []
    return _rubric_gaps(head["requirements"])


def _rubric_gaps(r: dict) -> list[dict]:
    gaps: list[dict] = []

    def gap(name, blocking, why):
        gaps.append({"name": name, "blocking": blocking, "why": why})

    def txt(field):
        return (r.get(field) or "") if isinstance(r.get(field), str) else ""

    refused = "refus" in txt("budget_confidence").lower()
    if not (r.get("budget_amount_min") or r.get("budget_amount_max")) and not refused:
        gap("budget figure", True, "no numeric budget and the owner did not refuse")
    if refused:
        gap("budget refused", False, "owner declined a figure — recorded, not chased")
    if not txt("timeline").strip():
        gap("timeline", True, "no timeline recorded")
    if not txt("solution_scope").strip():
        gap("solution scope", True, "no scope decision recorded")
    scope = txt("solution_scope").lower()
    if any(w in scope for w in ("book", "appoint", "schedul")) \
            and not r.get("scheduling_requirements"):
        gap("scheduling mechanics", True,
            "scope includes booking but scheduling_requirements is empty")
    if any(w in scope for w in ("deposit", "payment", "prepay", "pay", "invoice")) \
            and not r.get("payment_collection_requirements"):
        gap("payment mechanics", False,
            "scope mentions money; payment_collection_requirements is empty — "
            "confirm whether the bot itself takes money")
    for c in r.get("fact_conflicts") or []:
        if "unresolved" in str(c).lower():
            gap("fact conflict", True, str(c)[:200])
    for ch in r.get("channel_readiness") or []:
        if isinstance(ch, dict) and "does not exist" in str(ch.get("readiness") or ""):
            gap("channel to build", False,
                f"{ch.get('channel')} does not exist yet — a build dependency")
    return gaps
