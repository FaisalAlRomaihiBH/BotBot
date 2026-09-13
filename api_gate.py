# api_gate.py — the single doorway every Claude API call in this project
# walks through. Three jobs, all born from one incident (2026-09-13: a lab
# batch ate the whole rate limit and the live requirement chat stalled for
# over a minute on its first message):
#
#   1. PRIORITY. Calls declare interactive (a human is watching a spinner)
#      or background (lab sweeps, feature checks, analysis). Interactive
#      calls start immediately; background calls run at most
#      MAX_BACKGROUND at a time and PAUSE entirely while any interactive
#      call is in flight, so the humans' token budget is never eaten by
#      tests.
#   2. CAPACITY WAITS MADE VISIBLE. LLMs are built here with max_retries=0,
#      so a 429/529 surfaces instantly instead of hiding inside the SDK's
#      silent backoff. The gate does its own retrying, and while it waits
#      the call is marked "waiting for API capacity" — the dashboard shows
#      exactly that instead of a generic spinner.
#   3. THE QUEUE REGISTRY. Every call is registered (label, priority,
#      queued/running/waiting state, timings) and finished calls are kept
#      for the Rate Limiting tab, so "why is this slow" is answerable by
#      looking at a screen.
#
# STANDING RULE: never call ChatAnthropic(...).invoke() directly anywhere
# in this project. Build the model with make_llm() and call it with
# invoke() — new code included.
import itertools
import threading
import time

INTERACTIVE = "interactive"   # a person is waiting on this reply
BACKGROUND = "background"     # lab / analysis work; nobody is watching

# How many background calls may run at once. Deliberately small: the point
# is to leave most of the per-minute token budget free for interactive
# traffic. Lab batches still finish — they just stream through this window
# instead of firing all at once.
MAX_BACKGROUND = 2

# Capacity-retry policy: how long the gate keeps retrying a rate-limited
# call before giving up and raising. Generous, because the alternative to
# waiting is a failed interview turn.
MAX_CAPACITY_RETRIES = 10
BACKOFF_START = 5.0           # seconds, doubled each retry, capped below
BACKOFF_CAP = 60.0

_cv = threading.Condition()
_ids = itertools.count(1)
_live: dict[int, dict] = {}   # queued / running / waiting_capacity calls
_recent: list[dict] = []      # finished calls, newest first
_RECENT_KEEP = 80
_interactive_active = 0       # running or waiting-capacity interactive calls
_background_running = 0


def make_llm(model: str, **kwargs):
    """Build the project's standard ChatAnthropic. max_retries=0 is the
    contract with invoke(): rate limits must surface to the gate, which owns
    the waiting (and tells the UI about it) — the SDK must not hide them."""
    from langchain_anthropic import ChatAnthropic
    kwargs.setdefault("max_retries", 0)
    return ChatAnthropic(model=model, **kwargs)


def _is_capacity_error(e: Exception) -> bool:
    """A failure that means 'the API has no room right now, try later':
    a 429 rate limit or a 529 overloaded. Everything else is a real error
    and must propagate."""
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(e, anthropic.RateLimitError):
        return True
    status = getattr(e, "status_code", None)
    return isinstance(e, anthropic.APIStatusError) and status in (429, 529)


def _retry_after_seconds(e: Exception) -> float | None:
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value else None
    except Exception:
        return None


def _admit(entry: dict) -> None:
    """Block until this call may start. Interactive is never queued behind
    anything the gate controls; background waits for a free background slot
    AND for the interactive lane to be empty."""
    global _interactive_active, _background_running
    with _cv:
        if entry["priority"] == INTERACTIVE:
            _interactive_active += 1
        else:
            _cv.wait_for(lambda: _interactive_active == 0
                         and _background_running < MAX_BACKGROUND)
            _background_running += 1
        entry["state"] = "running"
        entry["started_ts"] = time.time()


def _release(entry: dict, error: str | None) -> None:
    global _interactive_active, _background_running
    with _cv:
        if entry["priority"] == INTERACTIVE:
            _interactive_active -= 1
        else:
            _background_running -= 1
        entry["state"] = "failed" if error else "done"
        entry["error"] = error
        entry["finished_ts"] = time.time()
        _live.pop(entry["id"], None)
        _recent.insert(0, entry)
        del _recent[_RECENT_KEEP:]
        _cv.notify_all()


def invoke(llm, messages, priority: str = BACKGROUND, label: str = "model call"):
    """The one way to call a model. Wraps llm.invoke() with priority
    admission, queue registration, and visible capacity waits."""
    entry = {"id": next(_ids), "label": label, "priority": priority,
             "model": getattr(llm, "model", None),
             "state": "queued", "queued_ts": time.time(),
             "started_ts": None, "finished_ts": None,
             "capacity_waits": 0, "capacity_wait_s": 0.0, "error": None}
    with _cv:
        _live[entry["id"]] = entry
    _admit(entry)
    try:
        backoff = BACKOFF_START
        for attempt in range(MAX_CAPACITY_RETRIES + 1):
            try:
                reply = llm.invoke(messages)
                _release(entry, None)
                return reply
            except Exception as e:
                if not _is_capacity_error(e) or attempt == MAX_CAPACITY_RETRIES:
                    _release(entry, f"{type(e).__name__}: {e}")
                    raise
                wait = _retry_after_seconds(e) or backoff
                backoff = min(backoff * 2, BACKOFF_CAP)
                # Background calls back off harder: their retry pressure must
                # never race an interactive call for the recovering budget.
                if priority == BACKGROUND:
                    wait = max(wait, 15.0)
                with _cv:
                    entry["state"] = "waiting_capacity"
                    entry["capacity_waits"] += 1
                    entry["capacity_wait_s"] += wait
                time.sleep(wait)
                with _cv:
                    entry["state"] = "running"
    finally:
        # invoke() exits only via _release above; this is belt-and-braces
        # for a truly unexpected path (e.g. KeyboardInterrupt mid-sleep).
        if entry["id"] in _live:
            _release(entry, entry.get("error") or "interrupted")


def snapshot() -> dict:
    """The Rate Limiting tab's payload: live calls, recent history, and the
    one flag the chat UI cares about — is an interactive call currently
    stuck waiting for API capacity?"""
    now = time.time()
    with _cv:
        live = [dict(e) for e in _live.values()]
        recent = [dict(e) for e in _recent]
        waiting = any(e["state"] == "waiting_capacity"
                      and e["priority"] == INTERACTIVE
                      for e in _live.values())
        bg_queued = sum(1 for e in _live.values() if e["state"] == "queued")
        counts = {"interactive_active": _interactive_active,
                  "background_running": _background_running,
                  "background_queued": bg_queued}
    for e in live:
        e["age_s"] = round(now - e["queued_ts"], 1)
        e["run_s"] = (round(now - e["started_ts"], 1)
                      if e["started_ts"] else None)
    for e in recent:
        e["run_s"] = (round(e["finished_ts"] - e["started_ts"], 1)
                      if e["started_ts"] and e["finished_ts"] else None)
        e["queued_s"] = (round(e["started_ts"] - e["queued_ts"], 1)
                         if e["started_ts"] else None)
    live.sort(key=lambda e: e["queued_ts"])
    return {"live": live, "recent": recent, "counts": counts,
            "waiting_capacity": waiting,
            "max_background": MAX_BACKGROUND}
