# evolve.py — BotBot's self-improvement loop.
#
# One CYCLE = persona -> interview -> evaluate -> improve prompt -> commit.
#   1. PERSONA:   an LLM invents a business owner (industry, personality, facts)
#                 and writes a fake WhatsApp export into uploads/ for them.
#   2. INTERVIEW: the persona talks to BotBot (intake.ask) until completion.
#   3. EVALUATE:  an LLM judge reviews the transcript + brief and lists problems.
#   4. IMPROVE:   an LLM rewrites interviewer_prompt.txt to fix those problems.
#                 GUARDRAILS: only the prompt file is ever touched; the new prompt
#                 must keep its template placeholders and stay under a size cap.
#   Every cycle ends in a git commit, then the next cycle starts with a fresh persona.
#
# Usage:  python evolve.py [number_of_cycles]     (default 1)
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from langchain_core.output_parsers import PydanticOutputParser

import intake  # reuses BotBot's llm, ask(), analyze_uploads(), schemas

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "evolve_runs"
HISTORY_FILE = RUNS_DIR / "history.json"
PROMPT_FILE = ROOT / "interviewer_prompt.txt"
MAX_TURNS = 30            # hard stop so a cycle can never run away

llm = intake.llm                 # Sonnet: personas + Birdie itself (via intake)
strong_llm = intake.ChatAnthropic(model="claude-opus-5")  # Opus: judge + improver
_blocks = intake._blocks_to_text


# ---------------- 1. PERSONA ----------------
class Persona(BaseModel):
    owner_name: str
    business_name: str
    industry: str
    personality: str          # e.g. "rambling and enthusiastic", "impatient one-word answers"
    background_facts: str     # everything the persona knows: services, prices, policies, problems
    whatsapp_export: str      # a realistic fake chat export for uploads/ (with gaps and dead-ends)


persona_parser = PydanticOutputParser(pydantic_object=Persona)

PERSONA_PROMPT = """You are creating a fictional business owner to test a
requirements-gathering chatbot. Invent a DIFFERENT kind of business and
personality than these recently used ones:
{used}

Create:
- a realistic owner and business (any country/industry; vary size and formality)
- personality: give them a distinct, challenging conversational style (examples:
  rambles off-topic, gives one-word answers, changes their mind, mixes two
  languages, vague about numbers, suspicious of technology). Pick ONE main trait.
- background_facts: a rich private fact sheet the persona will draw answers from —
  services with prices, hours, policies, staff, pain points, budget, timeline.
  Include 1-2 things the owner is genuinely unsure about.
- whatsapp_export: a fake WhatsApp export (15-25 messages, realistic timestamps)
  between customers and the business. Include BOTH clearly-answered inquiries AND
  2-3 dead-ends where staff said "let me check" or never followed up.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


def invoke_parsed(model, text, parser, tries=3):
    """invoke + parse, re-asking the model on malformed output instead of
    crashing the whole cycle (OutputParserException is the top crash cause)."""
    last = None
    for attempt in range(1, tries + 1):
        reply = model.invoke(text)
        try:
            return parser.parse(_blocks(reply.content))
        except Exception as e:
            last = e
            print(f"    parse failed (attempt {attempt}/{tries}): {type(e).__name__}")
    raise last


def generate_persona(used: list[str]) -> Persona:
    text = PERSONA_PROMPT.format(
        used="\n".join(f"- {u}" for u in used[-6:]) or "- (none yet)",
        format_instructions=persona_parser.get_format_instructions(),
    )
    return invoke_parsed(llm, text, persona_parser)


# ---------------- 2. INTERVIEW ----------------
PERSONA_TURN_PROMPT = """You are role-playing {owner_name}, owner of {business_name}
({industry}), talking to a chatbot consultant on a text chat.

Your personality: {personality}
Your private knowledge (answer ONLY from this; say you're not sure otherwise):
{background_facts}

Rules:
- Stay in character; reply the way this person types (length, tone, quirks).
- Answer the consultant's LAST message. One reply only, no narration.
- If asked to put files in an 'uploads' folder, reply that you added them
  (the files are already in place).
- If the consultant summarizes everything and asks you to confirm, check it
  against your knowledge: correct at most one or two real mistakes, otherwise
  confirm clearly.

Conversation so far:
{transcript}

Your reply:"""


def _ask_retry(question, chat_history, analysis_text, tries=3):
    """intake.ask with retries: one malformed-JSON turn from the interviewer
    model shouldn't kill a whole 30-turn interview."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return intake.ask(question, chat_history, analysis_text)
        except Exception as e:
            last = e
            print(f"    interviewer turn failed to parse "
                  f"(attempt {attempt}/{tries}): {type(e).__name__}")
    raise last


def run_interview(persona: Persona) -> dict:
    """Persona vs BotBot. Returns transcript, brief, and completion info."""
    # place the persona's fake export as the only file in uploads/
    uploads = ROOT / "uploads"
    if uploads.exists():
        shutil.rmtree(uploads)
    uploads.mkdir()
    (uploads / "whatsapp_export.txt").write_text(persona.whatsapp_export, encoding="utf-8")

    greeting = ("Hi! I help businesses figure out exactly what they need from a "
                "chatbot. Before we start — what's your name?")
    chat_history = [("ai", greeting)]
    transcript = [("Birdie", greeting)]
    analysis_text = intake.NO_MATERIALS
    analysis_obj = None
    final_turn = None

    for _ in range(MAX_TURNS):
        # persona replies to Birdie's last message
        convo = "\n".join(f"{who}: {msg}" for who, msg in transcript)
        reply = llm.invoke(PERSONA_TURN_PROMPT.format(
            owner_name=persona.owner_name, business_name=persona.business_name,
            industry=persona.industry, personality=persona.personality,
            background_facts=persona.background_facts, transcript=convo))
        owner_msg = _blocks(reply.content).strip()
        transcript.append((persona.owner_name, owner_msg))

        turn, raw = _ask_retry(owner_msg, chat_history, analysis_text)
        chat_history += [("human", owner_msg), ("ai", raw)]
        transcript.append(("Birdie", turn.next_message))
        final_turn = turn

        if turn.run_file_analysis:
            analysis_obj, _names = intake.analyze_uploads()
            if analysis_obj is not None:
                analysis_text = intake.analysis_to_prompt_text(analysis_obj)
            turn, raw = _ask_retry(
                "(System note: the analysis of the shared materials is now in your "
                "instructions. React to it: mention 1-2 useful things you learned, "
                "then ask about the first knowledge gap.)",
                chat_history, analysis_text)
            chat_history += [("human", "(files were analyzed)"), ("ai", raw)]
            transcript.append(("Birdie", turn.next_message))
            final_turn = turn

        if turn.interview_complete:
            break

    return {
        "transcript": transcript,
        "brief": final_turn.requirements.model_dump() if final_turn else None,
        "analysis": analysis_obj.model_dump() if analysis_obj else None,
        "completed": bool(final_turn and final_turn.interview_complete),
        "turns": len(transcript),
    }


# ---------------- 3. EVALUATE ----------------
class Finding(BaseModel):
    problem: str              # the specific observed problem
    excerpt: str              # the exact transcript lines where it happened, copied verbatim


class Evaluation(BaseModel):
    fact_capture: int         # 0-10: did every hard fact the owner gave land in the brief?
    no_repeats: int           # 0-10: never asked about things already answered
    evidence_use: int         # 0-10: used the file analysis; cited concrete examples
    naturalness: int          # 0-10: warm, human, adapted to the owner's personality
    completeness: int         # 0-10: brief usable by a developer; unknowns in open_items
    efficiency: int           # 0-10: no wasted/low-value questions; finished in sane turns
    findings: list[Finding]   # specific observed problems, each with its transcript excerpt
    top_improvement: str      # the single most valuable change to the interviewer prompt
    schema_suggestions: list[str]  # STRUCTURAL fixes needing code (new form fields, new
                                   # capabilities) — humans review these, the loop cannot apply them


eval_parser = PydanticOutputParser(pydantic_object=Evaluation)

EVAL_PROMPT = """You are a strict QA judge for an AI interviewer ("Birdie") that
gathers chatbot requirements from business owners. Judge THIS interview.

The persona's private fact sheet (what the owner knew and could have shared):
{facts}

The transcript:
{transcript}

The final requirements brief Birdie produced:
{brief}

Interview completed: {completed} (in {turns} transcript entries; fewer is better,
~20-30 is normal, non-completion is a serious failure)

Score each rubric dimension 0-10 harshly. In findings, list concrete problems;
for EACH finding, copy into its excerpt the exact transcript lines (speaker names
included, 1-4 lines) where the problem occurred — verbatim, no paraphrasing.
Compare the fact sheet against the brief for lost facts (for a lost fact, the
excerpt is the line where the owner stated it). Then name the ONE most valuable
prompt improvement.

Separately, in schema_suggestions, list STRUCTURAL problems that prompt wording
cannot fix — e.g. a kind of fact that recurringly has no proper form field (check
the brief's additional_notes and open_items for facts crammed somewhere wrong), or
a capability the interviewer lacks entirely. Suggest the field/capability to add.
Empty list if none.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


def evaluate(persona: Persona, result: dict) -> Evaluation:
    convo = "\n".join(f"{who}: {msg}" for who, msg in result["transcript"])
    return invoke_parsed(strong_llm, EVAL_PROMPT.format(
        facts=persona.background_facts, transcript=convo,
        brief=json.dumps(result["brief"], indent=2, ensure_ascii=False),
        completed=result["completed"], turns=result["turns"],
        format_instructions=eval_parser.get_format_instructions()), eval_parser)


def fmt_finding(f) -> str:
    """One finding -> '- problem' plus its indented transcript excerpt.
    Accepts Finding objects, dicts (from history.json) and old plain strings."""
    if isinstance(f, str):
        return f"- {f}"
    d = f if isinstance(f, dict) else f.model_dump()
    ex = "\n".join("    | " + line for line in d.get("excerpt", "").splitlines() if line.strip())
    return f"- {d['problem']}" + (f"\n{ex}" if ex else "")


def avg_score(ev: Evaluation) -> float:
    return round((ev.fact_capture + ev.no_repeats + ev.evidence_use +
                  ev.naturalness + ev.completeness + ev.efficiency) / 6, 2)


# ---------------- 4. IMPROVE (guardrailed) ----------------
IMPROVE_PROMPT = """You maintain the system prompt of "Birdie", an AI interviewer.
Below is its CURRENT prompt, then QA findings from one or more test interviews
with different business owners. Under each finding, indented lines starting
with | quote the exact chat moment where the problem occurred.

Rewrite the prompt to fix the problems found. STRICT rules:
- Prefer GENERAL principles over specific cases: fix the underlying habit, not
  the one business's quirk. One rule saying "cover every distinct service
  line's pricing separately" beats three rules naming tiffin, party trays and
  catering.
- You are encouraged to MERGE overlapping rules and DELETE rules that are
  redundant, over-specific to one past customer, or already implied by a more
  general rule. A shorter, sharper prompt is a better outcome than a longer one.
- Keep everything that clearly works; keep the existing tone and structure.
- The result MUST still contain the literal placeholders {{analysis}} and
  {{format_instructions}} exactly once each.
- HARD LIMIT: the result must be UNDER {cap} characters (current prompt is
  {cur}). If fixing everything would exceed that, keep only the
  highest-value rules and cut or merge the rest — an over-limit prompt is
  rejected outright, and an overlong prompt degrades the interviewer's
  output format, which is worse than any missing rule.
- Output ONLY the complete new prompt text. No commentary, no code fences.

=== CURRENT PROMPT ===
{prompt}

=== QA FINDINGS (with chat excerpts) ===
{findings}

=== EACH INTERVIEW'S TOP SUGGESTED IMPROVEMENT ===
{top}
"""


MAX_PROMPT_CHARS = 8000   # past ~2x this the interviewer's JSON output degrades
                          # until every turn fails to parse (seen at 19k chars)


def improve_prompt(findings: str, top: str) -> Optional[str]:
    """Rewrite the prompt from this cycle's findings.
    Returns a description of the change, or None if rejected by guardrails."""
    current = PROMPT_FILE.read_text(encoding="utf-8")
    reply = strong_llm.invoke(IMPROVE_PROMPT.format(
        cur=len(current), cap=MAX_PROMPT_CHARS,
        prompt=current, findings=findings, top=top))
    new = _blocks(reply.content).strip()
    if new.startswith("```"):
        new = new.strip("`").lstrip("text").strip()
    # ---- guardrails ----
    if new.count("{analysis}") != 1 or new.count("{format_instructions}") != 1:
        return None  # would break the template
    if len(new) < 500:
        return None  # gutted, or the model replied with commentary instead of a prompt
    if len(new) > MAX_PROMPT_CHARS:
        print(f"    improve rejected: {len(new)} chars exceeds the "
              f"{MAX_PROMPT_CHARS}-char cap")
        return None  # bloated prompts break the interviewer's output format
    if new == current.strip():
        return None
    PROMPT_FILE.write_text(new + "\n", encoding="utf-8")
    return f"prompt updated ({len(current)} -> {len(new)} chars)"


# ---------------- 5. RATCHET + orchestration ----------------
def git(*args) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def load_history() -> list:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def merge_histories(ours: list, theirs: list) -> list:
    """Union two diverged history.json lists by stamp; ours wins field-by-field
    on the same stamp, but an improve_done flag from either side sticks.
    Cycles are renumbered chronologically so numbering stays unique."""
    by_stamp = {}
    for e in list(theirs) + list(ours):
        prev = by_stamp.get(e["stamp"])
        if prev is not None:
            e = {**prev, **e,
                 "improve_done": bool(prev.get("improve_done") or e.get("improve_done"))}
        by_stamp[e["stamp"]] = e
    merged = sorted(by_stamp.values(), key=lambda e: e["stamp"])
    for i, e in enumerate(merged, 1):
        e["cycle"] = i
    return merged


def _resolve_conflict(path: str) -> None:
    # During a rebase, stage 2 is origin/main (a sibling's push) and stage 3 is
    # our replayed commit.
    if path == "evolve_runs/history.json":
        def stage(n):
            try:
                return json.loads(git("show", f":{n}:{path}"))
            except (json.JSONDecodeError, ValueError):
                return []
        merged = merge_histories(ours=stage(3), theirs=stage(2))
        (ROOT / path).write_text(
            json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    elif path == "evolve_runs/improve_pointer.json":
        done = []
        for n in (2, 3):
            try:
                done.append(json.loads(git("show", f":{n}:{path}"))["done"])
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
        (ROOT / path).write_text(json.dumps({"done": max(done, default=0)}))
    else:
        # keep our replayed change (e.g. the freshly improved prompt);
        # sibling-only changes never conflict in the first place
        git("checkout", "--theirs", "--", path)
    git("add", "--", path)


def sync_push(history: Optional[list] = None, max_tries=6) -> bool:
    """Push to origin/main, reconciling concurrent sibling pushes by rebasing
    and merging the shared JSON state instead of dropping either side.
    Refreshes `history` in place from the reconciled file."""
    ok = False
    for _ in range(max_tries):
        out = git("push", "origin", "main")
        if not any(m in out for m in ("[rejected]", "[remote rejected]", "failed to push")):
            ok = True
            break
        print("    push rejected (sibling routine pushed first) — rebasing...")
        git("fetch", "origin", "main")
        git("rebase", "origin/main")
        for _ in range(50):  # one iteration per conflicted replayed commit
            conflicted = [p for p in
                          git("diff", "--name-only", "--diff-filter=U").splitlines() if p]
            if not conflicted:
                break
            for p in conflicted:
                _resolve_conflict(p)
            git("-c", "core.editor=true", "rebase", "--continue")
        if "rebase" in git("status").lower() and "rebase in progress" in git("status"):
            git("rebase", "--abort")  # give up on this attempt, retry from scratch
    if not ok:
        print("!!! sync_push: could not push after retries — work is committed locally only.")
    if history is not None:
        history[:] = load_history()
    return ok


def run_cycle(cycle_no: int, history: list, improve: bool = True) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / f"cycle_{stamp}_{cycle_no}"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run_cycle_inner(cycle_no, stamp, run_dir, history, improve)
    except Exception:
        # leave a diagnosable trace next to the persona instead of a silent
        # orphan directory; the caller commits it
        (run_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _run_cycle_inner(cycle_no: int, stamp: str, run_dir: Path,
                     history: list, improve: bool) -> dict:
    used = [h["persona"] for h in history]
    print(f"\n=== CYCLE {cycle_no}: generating persona...")
    persona = generate_persona(used)
    print(f"    {persona.owner_name} — {persona.business_name} ({persona.industry})")
    print(f"    personality: {persona.personality}")
    (run_dir / "persona.json").write_text(persona.model_dump_json(indent=2), encoding="utf-8")

    print("    interviewing...")
    result = run_interview(persona)
    (run_dir / "interview.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"    completed={result['completed']} in {result['turns']} entries")

    print("    evaluating...")
    ev = evaluate(persona, result)
    score = avg_score(ev)
    (run_dir / "evaluation.json").write_text(ev.model_dump_json(indent=2), encoding="utf-8")
    print(f"    score: {score}/10 | findings: {len(ev.findings)}")
    for f in ev.findings[:5]:
        print(f"      - {f.problem[:120]}")
    if ev.schema_suggestions:
        print(f"    SCHEMA SUGGESTIONS (need human review):")
        for s in ev.schema_suggestions:
            print(f"      * {s[:150]}")

    # ---- IMPROVE: fix this cycle's problems, then move on to a new persona ----
    # (skipped in register-only mode: a separate hourly pass applies the fixes)
    improved = None
    if improve:
        print("    improving prompt from this interview's findings...")
        improved = improve_prompt("\n".join(fmt_finding(f) for f in ev.findings),
                                  ev.top_improvement)
        print(f"    {improved or 'improvement rejected by guardrails / no change'}")

    entry = {
        "cycle": cycle_no, "stamp": stamp,
        "persona": f"{persona.industry} / {persona.personality}",
        "score": score, "completed": result["completed"],
        "improved": bool(improved),
        "top_improvement": ev.top_improvement,
        "findings": [f.model_dump() for f in ev.findings],
        "schema_suggestions": ev.schema_suggestions,
    }
    history.append(entry)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    git("add", "-A")
    git("commit", "-m",
        f"evolve cycle {stamp}: {persona.industry} score {score}/10"
        + (" [prompt improved]" if improved else "")
        + "\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>")
    print(f"    committed. cycle done.")
    return entry


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    history = load_history()
    start = len(history) + 1
    consecutive_failures = 0
    for i in range(start, start + n):
        try:
            run_cycle(i, history)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            print(f"\n!!! cycle {i} CRASHED: {type(e).__name__}: {e}")
            git("checkout", "--", "interviewer_prompt.txt")  # discard any half-applied change
            if consecutive_failures >= 2:
                print("!!! two consecutive failures — halting the batch for safety.")
                break
            print("!!! continuing with next cycle...")
        time.sleep(2)

    print("\n=== history ===")
    for h in history:
        print(f"  cycle {h['cycle']}: {h['score']}/10  {h['persona'][:60]}"
              + ("  [improved]" if h["improved"] else ""))

    print("\npushing results to GitHub...")
    print("pushed" if sync_push(history) else "PUSH FAILED")
