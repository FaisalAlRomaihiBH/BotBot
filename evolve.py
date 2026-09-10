# evolve.py — BotBot's self-improvement loop.
#
# One CYCLE = persona -> interview -> evaluate -> improve prompt -> commit.
#   1. PERSONA:   an LLM invents a business owner (industry, personality, facts)
#                 and writes a fake WhatsApp export into uploads/ for them.
#   2. INTERVIEW: the persona talks to BotBot (intake.ask) until completion.
#   3. EVALUATE:  an LLM judge scores the transcript + brief against a fixed rubric.
#   4. IMPROVE:   an LLM rewrites interviewer_prompt.txt based on the findings.
#                 GUARDRAILS: only the prompt file is ever touched; the new prompt
#                 must keep its template placeholders and stay under a size cap.
#   5. RATCHET:   every cycle is a git commit. If a cycle's score drops sharply
#                 below the previous one, the last prompt change is reverted.
#
# Usage:  python evolve.py [number_of_cycles]     (default 1)
import json
import shutil
import subprocess
import sys
import time
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
BENCHMARK_FILE = RUNS_DIR / "benchmark_persona.json"  # FIXED persona for fair regression tests
MAX_TURNS = 30            # hard stop so a cycle can never run away
MAX_PROMPT_CHARS = 9000   # cap prompt bloat: improver must stay concise
SCORE_DROP_LIMIT = 1.5    # avg-score drop vs previous cycle that triggers revert
BENCHMARK_EVERY = 3       # every Nth cycle, re-run the benchmark persona as regression test

llm = intake.llm
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


def generate_persona(used: list[str]) -> Persona:
    text = PERSONA_PROMPT.format(
        used="\n".join(f"- {u}" for u in used[-6:]) or "- (none yet)",
        format_instructions=persona_parser.get_format_instructions(),
    )
    reply = llm.invoke(text)
    return persona_parser.parse(_blocks(reply.content))


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

        turn, raw = intake.ask(owner_msg, chat_history, analysis_text)
        chat_history += [("human", owner_msg), ("ai", raw)]
        transcript.append(("Birdie", turn.next_message))
        final_turn = turn

        if turn.run_file_analysis:
            analysis_obj, _names = intake.analyze_uploads()
            if analysis_obj is not None:
                analysis_text = intake.analysis_to_prompt_text(analysis_obj)
            turn, raw = intake.ask(
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
class Evaluation(BaseModel):
    fact_capture: int         # 0-10: did every hard fact the owner gave land in the brief?
    no_repeats: int           # 0-10: never asked about things already answered
    evidence_use: int         # 0-10: used the file analysis; cited concrete examples
    naturalness: int          # 0-10: warm, human, adapted to the owner's personality
    completeness: int         # 0-10: brief usable by a developer; unknowns in open_items
    efficiency: int           # 0-10: no wasted/low-value questions; finished in sane turns
    findings: list[str]       # specific observed problems, each citing the transcript
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

Score each rubric dimension 0-10 harshly. In findings, list concrete problems and
QUOTE or reference the moment in the transcript. Compare the fact sheet against the
brief for lost facts. Then name the ONE most valuable prompt improvement.

Separately, in schema_suggestions, list STRUCTURAL problems that prompt wording
cannot fix — e.g. a kind of fact that recurringly has no proper form field (check
the brief's additional_notes and open_items for facts crammed somewhere wrong), or
a capability the interviewer lacks entirely. Suggest the field/capability to add.
Empty list if none.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


def evaluate(persona: Persona, result: dict) -> Evaluation:
    convo = "\n".join(f"{who}: {msg}" for who, msg in result["transcript"])
    reply = llm.invoke(EVAL_PROMPT.format(
        facts=persona.background_facts, transcript=convo,
        brief=json.dumps(result["brief"], indent=2, ensure_ascii=False),
        completed=result["completed"], turns=result["turns"],
        format_instructions=eval_parser.get_format_instructions()))
    return eval_parser.parse(_blocks(reply.content))


def avg_score(ev: Evaluation) -> float:
    return round((ev.fact_capture + ev.no_repeats + ev.evidence_use +
                  ev.naturalness + ev.completeness + ev.efficiency) / 6, 2)


# ---------------- 4. IMPROVE (guardrailed) ----------------
IMPROVE_PROMPT = """You maintain the system prompt of "Birdie", an AI interviewer.
Below is its CURRENT prompt, then QA findings from the latest test interview.

Rewrite the prompt to address the findings. STRICT rules:
- Keep everything that already works; make SURGICAL changes, don't rewrite style.
- The result MUST still contain the literal placeholders {{analysis}} and
  {{format_instructions}} exactly once each.
- Do NOT grow the prompt beyond {cap} characters (current: {cur}). Prefer
  sharpening existing rules over adding new ones; merge or drop redundant rules.
- Output ONLY the complete new prompt text. No commentary, no code fences.

=== CURRENT PROMPT ===
{prompt}

=== QA FINDINGS ===
{findings}

=== MOST VALUABLE IMPROVEMENT ===
{top}
"""


def improve_prompt(ev: Evaluation) -> Optional[str]:
    """Returns a description of the change, or None if rejected by guardrails."""
    current = PROMPT_FILE.read_text(encoding="utf-8")
    reply = llm.invoke(IMPROVE_PROMPT.format(
        cap=MAX_PROMPT_CHARS, cur=len(current), prompt=current,
        findings="\n".join(f"- {f}" for f in ev.findings), top=ev.top_improvement))
    new = _blocks(reply.content).strip()
    if new.startswith("```"):
        new = new.strip("`").lstrip("text").strip()
    # ---- guardrails ----
    if new.count("{analysis}") != 1 or new.count("{format_instructions}") != 1:
        return None  # would break the template
    if len(new) > MAX_PROMPT_CHARS or len(new) < 500:
        return None  # bloated or gutted
    if new == current:
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


def run_cycle(cycle_no: int, history: list) -> dict:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / f"cycle_{stamp}"
    run_dir.mkdir(parents=True)

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
        print(f"      - {f[:120]}")
    if ev.schema_suggestions:
        print(f"    SCHEMA SUGGESTIONS (need human review):")
        for s in ev.schema_suggestions:
            print(f"      * {s[:150]}")

    # ratchet check BEFORE improving: did the PREVIOUS improvement hurt us?
    reverted = False
    if history and history[-1].get("improved") and score < history[-1]["score"] - SCORE_DROP_LIMIT:
        print(f"    REGRESSION: {history[-1]['score']} -> {score}. Reverting last prompt change.")
        git("checkout", history[-1]["commit_before"], "--", "interviewer_prompt.txt")
        reverted = True

    improved = None
    commit_before = git("rev-parse", "HEAD")
    if not reverted:
        print("    improving prompt...")
        improved = improve_prompt(ev)
        print(f"    {improved or 'improvement rejected by guardrails / no change'}")

    # ---- BENCHMARK regression test: same fixed persona vs the evolved prompt ----
    # Fair comparison (same opponent every time) — unlike cycle scores, which are
    # confounded by how hard each random persona happens to be.
    benchmark_score = None
    if not BENCHMARK_FILE.exists():
        BENCHMARK_FILE.write_text(persona.model_dump_json(indent=2), encoding="utf-8")
        benchmark_score = score  # this cycle's persona becomes the yardstick
        print(f"    benchmark persona seeded: {persona.owner_name} (baseline {score}/10)")
    elif improved and cycle_no % BENCHMARK_EVERY == 0:
        print("    benchmark regression test...")
        bench = Persona.model_validate_json(BENCHMARK_FILE.read_text(encoding="utf-8"))
        b_result = run_interview(bench)
        b_ev = evaluate(bench, b_result)
        benchmark_score = avg_score(b_ev)
        prev_bench = next((h["benchmark_score"] for h in reversed(history)
                           if h.get("benchmark_score") is not None), None)
        print(f"    benchmark: {benchmark_score}/10 (previous: {prev_bench})")
        if prev_bench is not None and benchmark_score < prev_bench - SCORE_DROP_LIMIT:
            print("    BENCHMARK REGRESSION — reverting this cycle's prompt change.")
            git("checkout", commit_before, "--", "interviewer_prompt.txt")
            improved = None
            reverted = True

    entry = {
        "cycle": cycle_no, "stamp": stamp,
        "persona": f"{persona.industry} / {persona.personality}",
        "score": score, "completed": result["completed"],
        "improved": bool(improved), "reverted": reverted,
        "commit_before": commit_before,
        "benchmark_score": benchmark_score,
        "top_improvement": ev.top_improvement,
        "schema_suggestions": ev.schema_suggestions,
    }
    history.append(entry)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

    git("add", "-A")
    git("commit", "-m",
        f"evolve cycle {cycle_no}: {persona.industry} score {score}/10"
        + (" [prompt improved]" if improved else "")
        + (" [REVERTED previous]" if reverted else "")
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
        b = f"  bench={h['benchmark_score']}" if h.get("benchmark_score") is not None else ""
        print(f"  cycle {h['cycle']}: {h['score']}/10{b}  {h['persona'][:60]}"
              + ("  [improved]" if h["improved"] else "")
              + ("  [reverted]" if h.get("reverted") else ""))

    print("\npushing results to GitHub...")
    print(git("push", "origin", "main") or "pushed")
