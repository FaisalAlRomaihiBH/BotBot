# store.py — the single authoritative durable store (SQLite, stdlib only).
#
# One file: orchestrator_data/botbot.db. WAL mode so the dashboard's reader
# threads never block a writer. Every public function opens a short-lived
# connection: the HTTP server is threaded, and sqlite3 connections must not
# cross threads.
import json
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "orchestrator_data"
DB_PATH = DATA_DIR / "botbot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'created',
  created_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  project_id TEXT PRIMARY KEY REFERENCES projects(id),
  bot_state TEXT, complete INTEGER NOT NULL DEFAULT 0, updated_ts REAL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
  conversation TEXT NOT NULL,       -- 'interview' | 'management'
  role TEXT NOT NULL,               -- 'owner' | 'bot' | 'operator' | 'supervisor' | 'system'
  text TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(
  project_id TEXT NOT NULL, rev INTEGER NOT NULL,
  requirements TEXT NOT NULL, created_ts REAL NOT NULL,
  PRIMARY KEY(project_id, rev));
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
  type TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
  source TEXT NOT NULL,             -- 'readiness_gap' | 'validation' | 'finding'
  decision_needed TEXT NOT NULL, blocking INTEGER NOT NULL DEFAULT 1,
  revision INTEGER,                 -- the revision this request is bound to
  status TEXT NOT NULL DEFAULT 'requested',  -- requested|resolved|superseded
  disposition TEXT, decided_by TEXT, created_ts REAL NOT NULL, resolved_ts REAL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
  revision INTEGER NOT NULL, actor TEXT NOT NULL, reason TEXT,
  status TEXT NOT NULL DEFAULT 'active',     -- active | invalidated
  ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invocations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, purpose TEXT NOT NULL,
  model TEXT, fresh_in INTEGER, cache_read INTEGER, cache_write INTEGER,
  out_tokens INTEGER, error TEXT, ts REAL NOT NULL);
"""


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(_SCHEMA)
    return con


def health() -> dict:
    """Observed store health for the honest status footer — a real write probe."""
    try:
        with _connect() as con:
            con.execute("SELECT 1")
        return {"store_ok": True, "path": str(DB_PATH)}
    except Exception as e:
        return {"store_ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------- projects ----------------
DEFAULT_PROJECT = "default"


def ensure_default_project() -> None:
    with _connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO projects(id, name, state, created_ts) "
            "VALUES(?,?,?,?)",
            (DEFAULT_PROJECT, "Default project", "created", time.time()))


def create_project(name: str) -> dict:
    pid = "proj_" + uuid.uuid4().hex[:10]
    with _connect() as con:
        con.execute("INSERT INTO projects(id, name, state, created_ts) VALUES(?,?,?,?)",
                    (pid, name, "created", time.time()))
    append_event(pid, "project.created", "operator", {"name": name})
    return {"id": pid, "name": name, "state": "created"}


def list_projects() -> list[dict]:
    ensure_default_project()
    with _connect() as con:
        rows = con.execute(
            "SELECT p.*, s.complete AS interview_complete FROM projects p "
            "LEFT JOIN sessions s ON s.project_id = p.id "
            "ORDER BY p.created_ts").fetchall()
    return [dict(r) for r in rows]


def get_project(pid: str) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def set_project_state(pid: str, state: str) -> None:
    with _connect() as con:
        con.execute("UPDATE projects SET state=? WHERE id=?", (state, pid))


# ---------------- sessions (serialized bot state) ----------------
def load_session(pid: str) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM sessions WHERE project_id=?", (pid,)).fetchone()
    if not r or not r["bot_state"]:
        return None
    return json.loads(r["bot_state"])


def save_session(pid: str, bot_state: dict, complete: bool) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO sessions(project_id, bot_state, complete, updated_ts) "
            "VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
            "bot_state=excluded.bot_state, complete=excluded.complete, "
            "updated_ts=excluded.updated_ts",
            (pid, json.dumps(bot_state, ensure_ascii=False), int(complete), time.time()))


def clear_session(pid: str) -> None:
    with _connect() as con:
        con.execute("DELETE FROM sessions WHERE project_id=?", (pid,))


# ---------------- messages ----------------
def add_message(pid: str, conversation: str, role: str, text: str) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO messages(project_id, conversation, role, text, ts) "
            "VALUES(?,?,?,?,?)", (pid, conversation, role, text, time.time()))


def get_messages(pid: str, conversation: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT role, text, ts FROM messages WHERE project_id=? AND "
            "conversation=? ORDER BY id", (pid, conversation)).fetchall()
    return [dict(r) for r in rows]


# ---------------- revisions ----------------
def head_revision(pid: str) -> int:
    with _connect() as con:
        r = con.execute("SELECT MAX(rev) AS m FROM revisions WHERE project_id=?",
                        (pid,)).fetchone()
    return r["m"] or 0


def commit_revision(pid: str, requirements: dict) -> int:
    """Immutable append. Skips the write when nothing changed since head."""
    body = json.dumps(requirements, ensure_ascii=False, sort_keys=True)
    with _connect() as con:
        last = con.execute(
            "SELECT rev, requirements FROM revisions WHERE project_id=? "
            "ORDER BY rev DESC LIMIT 1", (pid,)).fetchone()
        if last and last["requirements"] == body:
            return last["rev"]
        rev = (last["rev"] if last else 0) + 1
        con.execute("INSERT INTO revisions(project_id, rev, requirements, created_ts) "
                    "VALUES(?,?,?,?)", (pid, rev, body, time.time()))
    append_event(pid, "revision.committed", "controller", {"rev": rev})
    return rev


def get_revision(pid: str, rev: int | None = None) -> dict | None:
    with _connect() as con:
        if rev is None:
            r = con.execute("SELECT * FROM revisions WHERE project_id=? "
                            "ORDER BY rev DESC LIMIT 1", (pid,)).fetchone()
        else:
            r = con.execute("SELECT * FROM revisions WHERE project_id=? AND rev=?",
                            (pid, rev)).fetchone()
    if not r:
        return None
    return {"rev": r["rev"], "created_ts": r["created_ts"],
            "requirements": json.loads(r["requirements"])}


def list_revisions(pid: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute("SELECT rev, created_ts, LENGTH(requirements) AS size "
                           "FROM revisions WHERE project_id=? ORDER BY rev DESC",
                           (pid,)).fetchall()
    return [dict(r) for r in rows]


# ---------------- events ----------------
def append_event(pid: str | None, etype: str, actor: str, payload: dict | None = None) -> None:
    with _connect() as con:
        con.execute("INSERT INTO events(project_id, type, actor, payload, ts) "
                    "VALUES(?,?,?,?,?)",
                    (pid, etype, actor,
                     json.dumps(payload or {}, ensure_ascii=False), time.time()))


def recent_events(pid: str, limit: int = 40) -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT seq, type, actor, payload, ts FROM events WHERE project_id=? "
            "ORDER BY seq DESC LIMIT ?", (pid, limit)).fetchall()
    return [dict(r) | {"payload": json.loads(r["payload"] or "{}")} for r in rows]


def last_provider_event() -> dict | None:
    """The most recent model call: its purpose, when, and whether it errored.
    This is what the status footer shows instead of a hardcoded green dot."""
    with _connect() as con:
        r = con.execute("SELECT purpose, model, ts, error FROM invocations "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


# ---------------- invocations (usage accounting) ----------------
def add_invocation(pid: str | None, purpose: str, model: str | None,
                   usage: dict | None, error: str | None = None) -> None:
    u = usage or {}
    with _connect() as con:
        con.execute(
            "INSERT INTO invocations(project_id, purpose, model, fresh_in, "
            "cache_read, cache_write, out_tokens, error, ts) VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, purpose, model, u.get("fresh_in"), u.get("cache_read"),
             u.get("cache_write"), u.get("out"), error, time.time()))


def usage_totals(pid: str) -> dict:
    with _connect() as con:
        r = con.execute(
            "SELECT COALESCE(SUM(fresh_in),0) f, COALESCE(SUM(cache_read),0) cr, "
            "COALESCE(SUM(cache_write),0) cw, COALESCE(SUM(out_tokens),0) o, "
            "COUNT(*) n FROM invocations WHERE project_id=?", (pid,)).fetchone()
    return {"fresh_in": r["f"], "cache_read": r["cr"], "cache_write": r["cw"],
            "out": r["o"], "invocations": r["n"]}


# ---------------- reviews ----------------
def open_reviews(pid: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute("SELECT * FROM reviews WHERE project_id=? AND "
                           "status='requested' ORDER BY id", (pid,)).fetchall()
    return [dict(r) for r in rows]


def all_reviews(pid: str) -> list[dict]:
    with _connect() as con:
        rows = con.execute("SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC",
                           (pid,)).fetchall()
    return [dict(r) for r in rows]


def add_review(pid: str, source: str, decision_needed: str, blocking: bool,
               revision: int) -> None:
    with _connect() as con:
        # One open request per decision text: re-running readiness must not
        # stack duplicates of the same gap.
        dup = con.execute(
            "SELECT id FROM reviews WHERE project_id=? AND decision_needed=? AND "
            "status='requested'", (pid, decision_needed)).fetchone()
        if dup:
            return
        con.execute(
            "INSERT INTO reviews(project_id, source, decision_needed, blocking, "
            "revision, created_ts) VALUES(?,?,?,?,?,?)",
            (pid, source, decision_needed, int(blocking), revision, time.time()))
    append_event(pid, "review.requested", "controller",
                 {"decision_needed": decision_needed, "blocking": blocking})


def resolve_review(pid: str, review_id: int, disposition: str, decided_by: str) -> bool:
    with _connect() as con:
        cur = con.execute(
            "UPDATE reviews SET status='resolved', disposition=?, decided_by=?, "
            "resolved_ts=? WHERE id=? AND project_id=? AND status='requested'",
            (disposition, decided_by, time.time(), review_id, pid))
        ok = cur.rowcount == 1
    if ok:
        append_event(pid, "review.resolved", "operator",
                     {"review_id": review_id, "disposition": disposition})
    return ok


def supersede_reviews(pid: str, up_to_revision: int) -> None:
    """Open requests bound to an older revision go stale when the spec moves."""
    with _connect() as con:
        con.execute("UPDATE reviews SET status='superseded' WHERE project_id=? AND "
                    "status='requested' AND revision < ?", (pid, up_to_revision))


# ---------------- approvals ----------------
def add_approval(pid: str, revision: int, actor: str, reason: str) -> None:
    with _connect() as con:
        con.execute("INSERT INTO approvals(project_id, revision, actor, reason, ts) "
                    "VALUES(?,?,?,?,?)", (pid, revision, actor, reason, time.time()))
    append_event(pid, "approval.recorded", "operator",
                 {"revision": revision, "reason": reason})


def active_approval(pid: str) -> dict | None:
    with _connect() as con:
        r = con.execute("SELECT * FROM approvals WHERE project_id=? AND "
                        "status='active' ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    return dict(r) if r else None


def invalidate_approvals(pid: str) -> None:
    with _connect() as con:
        cur = con.execute("UPDATE approvals SET status='invalidated' "
                          "WHERE project_id=? AND status='active'", (pid,))
        n = cur.rowcount
    if n:
        append_event(pid, "approval.invalidated", "controller", {"count": n})
