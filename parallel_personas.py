# parallel_personas.py — mass persona testing for RequirementsBot.
#
# Runs MANY simulated interviews at once: for each interview an LLM invents a
# business-owner persona and role-plays them against RequirementsBot until the
# interview completes; a judge then scores the transcript and lists problems.
#
# Each interview is fully independent (its own RequirementsBot, no uploads/
# folder involvement — personas paste example messages instead of files), so
# they parallelize safely with a thread pool.
#
# Usage:
#   python parallel_personas.py --count 10 --concurrency 5
#
# Output: parallel_runs/<stamp>/interview_<n>.json (persona, transcript, brief,
# evaluation) plus summary.json with scores and aggregated findings.
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from requirements_bot import RequirementsBot, _blocks_to_text

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
MAX_TURNS = 30  # hard stop so one interview can never run away

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ---------------- persona ----------------
class Persona(BaseModel):
    owner_name: str
    business_name: str
    industry: str
    personality: str          # a distinct, challenging conversational style
    background_facts: str     # everything the persona knows: services, prices, policies, problems
    whatsapp_export: str      # a realistic fake chat export placed in the interview's uploads dir


class PersonaBatch(BaseModel):
    personas: list[Persona]


PERSONA_BATCH_PROMPT = """You are creating fictional business owners to test a
requirements-gathering chatbot. Create {n} COMPLETELY DIFFERENT personas —
vary country, industry, business size, formality, and language style.

For each persona:
- a realistic owner and business
- personality: a distinct, challenging conversational style (examples: rambles
  off-topic, one-word answers, changes their mind, mixes two languages, vague
  about numbers, suspicious of technology, impatient, oversharing). One main
  trait each, all different from each other.
- background_facts: a rich private fact sheet the persona draws answers from —
  services with prices, hours, policies, staff, pain points, budget, timeline.
  Include 1-2 things the owner is genuinely unsure about.
- whatsapp_export: a fake WhatsApp export (15-25 messages, realistic timestamps)
  between customers and the business. Include BOTH clearly-answered inquiries
  AND 2-3 dead-ends where staff said "let me check" or never followed up.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


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


# ---------------- evaluation ----------------
class Finding(BaseModel):
    problem: str              # the specific observed problem
    excerpt: str              # the exact transcript lines where it happened, copied verbatim


class Evaluation(BaseModel):
    fact_capture: int         # 0-10: did every hard fact the owner gave land in the brief?
    no_repeats: int           # 0-10: never asked about things already answered
    naturalness: int          # 0-10: warm, human, adapted to the owner's personality
    completeness: int         # 0-10: brief usable by a developer; unknowns in open_items
    efficiency: int           # 0-10: no wasted/low-value questions; finished in sane turns
    findings: list[Finding]   # specific observed problems, each with its transcript excerpt
    top_improvement: str      # the single most valuable change to the interviewer prompt
    code_suggestions: list[str]  # STRUCTURAL problems prompt wording cannot fix (missing form
                                 # fields, crashes, missing capabilities) — fixed by a coding agent


EVAL_PROMPT = """You are a strict QA judge for an AI interviewer ("RequirementsBot")
that gathers chatbot requirements from business owners. Judge THIS interview.

The persona's private fact sheet (what the owner knew and could have shared):
{facts}

The materials the owner "uploaded" (the interviewer analyzed these; facts in
the brief may legitimately come from here, not only from the transcript):
{materials}

The interviewer's analysis of those materials:
{analysis}

The transcript:
{transcript}

The final requirements brief produced:
{brief}

Interview completed: {completed} (in {turns} transcript entries; fewer is better,
~20-30 is normal, non-completion is a serious failure)

Score each rubric dimension 0-10 harshly. In findings, list concrete problems;
for EACH finding, copy into its excerpt the exact transcript lines (speaker names
included, 1-4 lines) where the problem occurred — verbatim, no paraphrasing.
Compare the fact sheet against the brief for lost facts. Then name the ONE most
valuable prompt improvement.

Separately, in code_suggestions, list STRUCTURAL problems that prompt wording
cannot fix — e.g. a kind of fact that recurringly has no proper form field
(check the brief's additional_notes and open_items for facts crammed somewhere
wrong), a crash or malformed output, or a capability the interviewer lacks
entirely. Suggest the field/capability to add. Empty list if none.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


class ParallelPersonaRunner:
    """Generates personas, runs their interviews concurrently, judges them."""

    def __init__(self, count: int, concurrency: int,
                 persona_model: str = "claude-sonnet-5",
                 judge_model: str = "claude-sonnet-5"):
        self.count = count
        self.concurrency = concurrency
        # One shared client for personas/judging is fine — invoke() is thread-safe.
        from langchain_anthropic import ChatAnthropic
        self.persona_llm = ChatAnthropic(model=persona_model, max_tokens=8000,
                                         max_retries=6)
        self.judge_llm = ChatAnthropic(model=judge_model, max_tokens=8000,
                                       max_retries=6)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = RUNS_DIR / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- pipeline steps ----------------
    def generate_personas(self) -> list[Persona]:
        """Batch-generate personas, 10 per model call."""
        parser = PydanticOutputParser(pydantic_object=PersonaBatch)
        personas: list[Persona] = []
        while len(personas) < self.count:
            n = min(10, self.count - len(personas))
            reply = self.persona_llm.invoke(PERSONA_BATCH_PROMPT.format(
                n=n, format_instructions=parser.get_format_instructions()))
            batch = parser.parse(_blocks_to_text(reply.content)).personas[:n]
            personas.extend(batch)
            log(f"[personas] {len(personas)}/{self.count} generated")
        return personas

    def run_interview(self, idx: int, persona: Persona) -> dict:
        # Each interview gets its OWN uploads folder so parallel runs never
        # fight over one shared directory; the persona's fake export goes there.
        uploads = self.run_dir / f"uploads_{idx:03d}"
        uploads.mkdir(exist_ok=True)
        (uploads / "whatsapp_export.txt").write_text(
            persona.whatsapp_export, encoding="utf-8")
        bot = RequirementsBot(uploads_dir=uploads)
        transcript = [("Bot", RequirementsBot.GREETING)]

        for _ in range(MAX_TURNS):
            convo = "\n".join(f"{who}: {msg}" for who, msg in transcript)
            reply = self.persona_llm.invoke(PERSONA_TURN_PROMPT.format(
                owner_name=persona.owner_name, business_name=persona.business_name,
                industry=persona.industry, personality=persona.personality,
                background_facts=persona.background_facts, transcript=convo))
            owner_msg = _blocks_to_text(reply.content).strip()
            transcript.append((persona.owner_name, owner_msg))

            messages, turn = bot.send(owner_msg)
            for msg in messages:
                transcript.append(("Bot", msg))
            if bot.complete:
                break

        return {
            "transcript": transcript,
            "brief": turn.requirements.model_dump(),
            "analysis": bot.analysis.model_dump() if bot.analysis else None,
            "completed": bot.complete,
            "turns": len(transcript),
        }

    def evaluate(self, persona: Persona, result: dict) -> Evaluation:
        parser = PydanticOutputParser(pydantic_object=Evaluation)
        convo = "\n".join(f"{who}: {msg}" for who, msg in result["transcript"])
        reply = self.judge_llm.invoke(EVAL_PROMPT.format(
            facts=persona.background_facts,
            materials=persona.whatsapp_export,
            analysis=json.dumps(result.get("analysis"), indent=2, ensure_ascii=False),
            transcript=convo,
            brief=json.dumps(result["brief"], indent=2, ensure_ascii=False),
            completed=result["completed"], turns=result["turns"],
            format_instructions=parser.get_format_instructions()))
        return parser.parse(_blocks_to_text(reply.content))

    # ---------------- one full interview + judge ----------------
    def run_one(self, idx: int, persona: Persona) -> dict:
        tag = f"[{idx:03d} {persona.industry[:30]}]"
        try:
            log(f"{tag} interviewing...")
            result = self.run_interview(idx, persona)
            log(f"{tag} completed={result['completed']} in {result['turns']} entries; judging...")
            ev = self.evaluate(persona, result)
            score = round((ev.fact_capture + ev.no_repeats + ev.naturalness
                           + ev.completeness + ev.efficiency) / 5, 2)
            record = {
                "index": idx,
                "persona": persona.model_dump(),
                "interview": result,
                "evaluation": ev.model_dump(),
                "score": score,
                "error": None,
            }
            log(f"{tag} score {score}/10, {len(ev.findings)} findings")
        except Exception as e:
            record = {"index": idx, "persona": persona.model_dump(),
                      "interview": None, "evaluation": None, "score": None,
                      "error": f"{type(e).__name__}: {e}"}
            log(f"{tag} FAILED: {record['error']}")
        (self.run_dir / f"interview_{idx:03d}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False, default=list),
            encoding="utf-8")
        return record

    # ---------------- orchestration ----------------
    def run(self) -> dict:
        log(f"=== generating {self.count} personas...")
        personas = self.generate_personas()

        log(f"=== running {self.count} interviews, {self.concurrency} at a time...")
        records = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(self.run_one, i, p)
                       for i, p in enumerate(personas, 1)]
            for future in as_completed(futures):
                records.append(future.result())
        records.sort(key=lambda r: r["index"])

        scored = [r for r in records if r["score"] is not None]
        summary = {
            "count": self.count,
            "succeeded": len(scored),
            "failed": len(records) - len(scored),
            "completed_interviews": sum(1 for r in scored if r["interview"]["completed"]),
            "avg_score": round(sum(r["score"] for r in scored) / len(scored), 2) if scored else None,
            "worst": [{"index": r["index"], "industry": r["persona"]["industry"],
                       "score": r["score"],
                       "top_improvement": r["evaluation"]["top_improvement"]}
                      for r in sorted(scored, key=lambda r: r["score"])[:10]],
            "all_scores": {r["index"]: r["score"] for r in records},
            "errors": {r["index"]: r["error"] for r in records if r["error"]},
            "code_suggestions": sorted({s for r in scored
                                        for s in r["evaluation"].get("code_suggestions", [])}),
        }
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\n=== done: {summary['succeeded']}/{self.count} judged, "
            f"avg score {summary['avg_score']}/10 -> {self.run_dir}")
        return summary


class PromptImprover:
    """Applies what the run learned: rewrites interviewer_prompt.txt from the
    aggregated judge findings, then commits and pushes — guardrailed so a bad
    rewrite can never break the bot."""

    IMPROVE_PROMPT = """You maintain the system prompt of "RequirementsBot", an AI
interviewer. Below is its CURRENT prompt, then QA findings from many test
interviews with different business owners. Under each finding, indented lines
starting with | quote the exact chat moment where the problem occurred.

Rewrite the prompt to fix the problems found. STRICT rules:
- Prefer GENERAL principles over specific cases: fix the underlying habit, not
  one business's quirk.
- MERGE overlapping rules and DELETE rules that are redundant, over-specific,
  or implied by a more general rule. A shorter, sharper prompt is a better
  outcome than a longer one.
- Keep everything that clearly works; keep the existing tone and structure.
- The result MUST still contain the literal placeholders {{analysis}} and
  {{format_instructions}} exactly once each.
- Aim to stay near the current size ({cur} characters); never exceed 9000,
  and treat 8000 as the working budget — when the current prompt is already
  near it, every rule you add must be paid for by condensing or cutting
  elsewhere.
- Output ONLY the complete new prompt text. No commentary, no code fences.

=== CURRENT PROMPT ===
{prompt}

=== QA FINDINGS (with chat excerpts) ===
{findings}

=== EACH INTERVIEW'S TOP SUGGESTED IMPROVEMENT ===
{top}
"""

    def __init__(self, llm, run_dir: Path | None = None):
        self.llm = llm
        self.prompt_file = RequirementsBot.PROMPT_FILE
        self.run_dir = run_dir

    @staticmethod
    def _violation(new: str, current: str) -> str | None:
        """Which guardrail a candidate rewrite breaks, or None if it's fine."""
        if new.count("{analysis}") != 1 or new.count("{format_instructions}") != 1:
            return ("it must contain the literal placeholders {analysis} and "
                    "{format_instructions} exactly once each")
        if len(new) < 500:
            return "it is far too short — output the COMPLETE prompt, not commentary"
        if len(new) > 9000:
            return (f"it is {len(new)} characters, over the 9000 hard limit — "
                    "condense: merge overlapping rules and cut the least valuable "
                    "ones instead of only adding new text")
        if new == current.strip():
            return "it is identical to the current prompt"
        return None

    def improve(self, records: list[dict]) -> str | None:
        """Rewrite the prompt from the run's findings. Returns a description
        of the change, or None if there was nothing to fix / guardrails hit.
        A rejected attempt is retried with the violated rule quoted back."""
        findings, tops = [], []
        for r in records:
            if not r["evaluation"]:
                continue
            tops.append(f"- {r['evaluation']['top_improvement']}")
            for f in r["evaluation"]["findings"]:
                excerpt = "\n".join("    | " + line
                                    for line in f["excerpt"].splitlines() if line.strip())
                findings.append(f"- {f['problem']}" + (f"\n{excerpt}" if excerpt else ""))
        if not findings:
            return None

        current = self.prompt_file.read_text(encoding="utf-8")
        base_prompt = self.IMPROVE_PROMPT.format(
            cur=len(current), prompt=current,
            findings="\n".join(findings), top="\n".join(tops))
        feedback = ""
        for attempt in range(1, 4):
            reply = self.llm.invoke(base_prompt + feedback)
            new = _blocks_to_text(reply.content).strip()
            if new.startswith("```"):
                new = new.strip("`").lstrip("text").strip()
            why = self._violation(new, current)
            if why is None:
                self.prompt_file.write_text(new + "\n", encoding="utf-8")
                return f"prompt updated ({len(current)} -> {len(new)} chars, attempt {attempt})"
            log(f"    improve attempt {attempt} rejected: {why}")
            if self.run_dir:
                (self.run_dir / f"rejected_rewrite_{attempt}.txt").write_text(
                    new, encoding="utf-8")
            feedback = (f"\n\n=== YOUR PREVIOUS ATTEMPT WAS REJECTED ===\n"
                        f"Reason: {why}.\nProduce a corrected complete prompt.")
        return None


def _git(*args) -> str:
    import subprocess
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def improve_and_push(runner: "ParallelPersonaRunner", records: list[dict],
                     summary: dict) -> None:
    log("=== improving the prompt from this run's findings...")
    improver = PromptImprover(runner.judge_llm, run_dir=runner.run_dir)
    change = improver.improve(records)
    log(f"    {change or 'no change (nothing to fix, or guardrails rejected the rewrite)'}")
    if change:
        _git("add", str(RequirementsBot.PROMPT_FILE))
        _git("commit", "-m",
             f"parallel personas x{summary['count']}: avg {summary['avg_score']}/10, "
             f"{change}\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>")
        log("    pushing...")
        log("    " + (_git("push", "origin", "main") or "pushed"))


CODE_FIX_PROMPT = """You are maintaining RequirementsBot in this repository
(requirements_bot.py, models.py, main.py, interviewer_prompt.txt). A mass
persona-testing run of the bot surfaced STRUCTURAL problems that prompt
wording cannot fix. Fix them in code now:

{suggestions}

Rules:
- Read the relevant files first and make the smallest correct change for each
  problem (e.g. a new Optional field on BusinessRequirements in models.py plus
  a short mention in interviewer_prompt.txt, or a bug fix in
  requirements_bot.py). Skip any suggestion that is wrong, already handled,
  or too risky to apply blindly — and say so.
- Never remove existing form fields or break to_dict/from_dict compatibility.
- Verify with a syntax check (python -c "import requirements_bot, models")
  before committing.
- Commit the changes with a clear message ending in
  "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
  and push to origin main. If you changed nothing, commit nothing.
"""


def fix_code_issues(summary: dict) -> None:
    """Hand the run's structural findings to a headless Claude Code agent that
    edits the code, verifies it, commits and pushes."""
    import shutil
    import subprocess
    suggestions = summary.get("code_suggestions") or []
    if not suggestions:
        log("=== no code-level suggestions from this run.")
        return
    exe = shutil.which("claude")
    if not exe:
        log("=== claude CLI not found; code suggestions saved in summary.json only.")
        return
    log(f"=== fixing {len(suggestions)} code-level suggestion(s) via claude -p ...")
    prompt = CODE_FIX_PROMPT.format(
        suggestions="\n".join(f"- {s}" for s in suggestions))
    r = subprocess.run(
        [exe, "-p", prompt,
         "--permission-mode", "acceptEdits",
         "--allowedTools", "Bash(python*) Bash(git add:*) Bash(git commit:*) Bash(git push:*)"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800)
    log(r.stdout.strip()[-2000:] or "(no output)")
    if r.returncode != 0:
        log(f"    code-fix agent exited {r.returncode}: {r.stderr.strip()[-500:]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5, help="number of interviews")
    ap.add_argument("--concurrency", type=int, default=5,
                    help="how many run at the same time")
    ap.add_argument("--no-improve", action="store_true",
                    help="only measure; don't rewrite the prompt or push")
    args = ap.parse_args()
    runner = ParallelPersonaRunner(args.count, args.concurrency)
    run_summary = runner.run()
    if not args.no_improve:
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted(runner.run_dir.glob("interview_*.json"))]
        improve_and_push(runner, records, run_summary)
        fix_code_issues(run_summary)
