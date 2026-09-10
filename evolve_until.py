# evolve_until.py — run evolve cycles back-to-back until a stop time (default 18:00
# today). Cycles only REGISTER problems; once per hour an IMPROVE pass fixes the
# prompt from everything registered since the last pass and writes a PDF report
# (problems -> fixes) into evolve_runs/reports/ for emailing.
#
# Usage: python evolve_until.py [HH:MM]
import json
import sys
import time
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

import evolve

REPORTS_DIR = evolve.RUNS_DIR / "reports"
POINTER_FILE = evolve.RUNS_DIR / "improve_pointer.json"  # how many history entries are already fixed
IMPROVE_INTERVAL = 3600  # seconds between improve passes


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_persona(persona: str, limit=60) -> str:
    """'industry / long personality essay' -> just the business type."""
    industry = persona.partition(" / ")[0].strip()
    return industry[:limit].rstrip() + ("…" if len(industry) > limit else "")


SUMMARY_PROMPT = """You write a plain-language hourly digest of QA test results
for a business owner with no technical background. Below are this hour's test
interviews of their AI interviewer bot ("Birdie"), each with QA findings, plus
a description of the prompt fix applied (if any).

Return ONLY JSON, no other text, in exactly this shape:
{{"interviews": [{{"cycle": <n>, "issue": "<ONE short simple sentence: the
single biggest problem in that interview>"}}],
"fix": "<ONE short simple sentence: what was improved this hour, or empty
string if nothing>",
"attention": ["<up to 3 short simple sentences: things that need a human
decision>"]}}

Keep every sentence under 20 words. No jargon, no field names, no quotes from
transcripts.

=== THIS HOUR'S INTERVIEWS ===
{batch}

=== FIX APPLIED ===
{fix}
"""


def summarize_batch(batch, improved_desc) -> dict:
    """One-line plain-language summaries via the strong model, with a
    deterministic fallback so the report always renders."""
    fallback = {
        "interviews": [{"cycle": h["cycle"],
                        "issue": (h.get("top_improvement") or "")[:160]}
                       for h in batch],
        "fix": improved_desc or "",
        "attention": [s[:160] for h in batch
                      for s in h.get("schema_suggestions", [])][:3],
    }
    try:
        raw = "\n\n".join(
            f"Cycle {h['cycle']} — {h['persona']} — score {h['score']}/10\n"
            + "\n".join(f"- {f['problem'] if isinstance(f, dict) else f}"
                         for f in h.get("findings", []))
            + (f"\nNeeds human decision: {'; '.join(h.get('schema_suggestions', []))}"
               if h.get("schema_suggestions") else "")
            for h in batch)
        reply = evolve.strong_llm.invoke(SUMMARY_PROMPT.format(
            batch=raw, fix=improved_desc or "(no prompt change this hour)"))
        text = evolve._blocks(reply.content).strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        out = json.loads(text)
        issues = {i["cycle"]: i["issue"] for i in out.get("interviews", [])}
        return {
            "interviews": [{"cycle": h["cycle"],
                            "issue": issues.get(h["cycle"]) or "(no summary)"}
                           for h in batch],
            "fix": out.get("fix", ""),
            "attention": [a for a in out.get("attention", []) if a][:3],
        }
    except Exception as e:
        print(f">>> summary model failed ({type(e).__name__}) — using fallback text")
        return fallback


def write_pdf(path, batch, improved_desc, old_prompt, new_prompt):
    """A one-page, all-bullets digest a non-technical reader can skim."""
    styles = getSampleStyleSheet()
    h1 = styles["Title"]
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceBefore=14,
                        textColor=colors.HexColor("#2b3a55"))
    body = ParagraphStyle("body", parent=styles["BodyText"],
                          fontSize=10.5, leading=15)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=14,
                            bulletIndent=2, spaceAfter=4)
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)

    def b(text):
        return Paragraph(esc(text), bullet, bulletText="•")

    s = summarize_batch(batch, improved_desc)
    avg = round(sum(h["score"] for h in batch) / len(batch), 2)
    story = [Paragraph("BotBot Evolve — Hourly Summary", h1),
             Paragraph(datetime.now().strftime("%A %d %B %Y, %H:%M"),
                       styles["BodyText"]),
             Spacer(1, 8)]

    story.append(Paragraph("This hour", h2))
    story.append(b(f"{len(batch)} test interview(s), average score {avg}/10."))
    story.append(b("The bot's instructions were improved this hour."
                   if improved_desc else "No instruction change this hour."))
    incomplete = [h for h in batch if not h["completed"]]
    if incomplete:
        story.append(b(f"{len(incomplete)} interview(s) did not finish — worth a look."))

    story.append(Paragraph("Each interview, in one line", h2))
    issue_by_cycle = {i["cycle"]: i["issue"] for i in s["interviews"]}
    for h in batch:
        story.append(b(f"{short_persona(h['persona'])} — {h['score']}/10. "
                       f"{issue_by_cycle.get(h['cycle'], '')}"))

    story.append(Paragraph("What got better", h2))
    story.append(b(s["fix"] if s["fix"]
                   else "Nothing this hour — the problems found are queued for the next fix."))

    if s["attention"]:
        story.append(Paragraph("Needs your decision", h2))
        for a in s["attention"]:
            story.append(b(a))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Full details (transcripts, findings, prompt changes) are in the "
        "BotBot repo under evolve_runs/.", styles["Italic"]))
    doc.build(story)


def improve_pass(history) -> str | None:
    """Improve from all not-yet-fixed history entries; write a PDF. Returns PDF path."""
    # migrate the legacy index-based pointer to per-entry flags (the index was
    # meaningless once concurrent routines started merging their histories)
    if POINTER_FILE.exists():
        done = json.loads(POINTER_FILE.read_text()).get("done", 0)
        for h in history[:done]:
            h.setdefault("improve_done", True)
        POINTER_FILE.unlink()
    batch = [h for h in history if not h.get("improve_done")]
    if not batch:
        print(">>> improve pass: nothing new registered, skipping.")
        return None
    print(f">>> improve pass: {len(batch)} new interview(s)...")
    findings = "\n\n".join(
        f"[{h['persona']}]\n" + "\n".join(evolve.fmt_finding(f) for f in h.get("findings", []))
        for h in batch)
    tops = "\n".join(f"- {h['top_improvement']}" for h in batch)
    old_prompt = evolve.PROMPT_FILE.read_text(encoding="utf-8")
    improved = evolve.improve_prompt(findings, tops)
    new_prompt = evolve.PROMPT_FILE.read_text(encoding="utf-8")
    print(f">>> {improved or 'improvement rejected by guardrails / no change'}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf = REPORTS_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    write_pdf(pdf, batch, improved, old_prompt, new_prompt)
    for h in batch:
        h["improve_done"] = True
    evolve.HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    evolve.git("add", "-A")
    evolve.git("commit", "-m",
               f"evolve hourly improve: {len(batch)} interviews"
               + (" [prompt improved]" if improved else " [no change]")
               + "\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>")
    evolve.sync_push(history)
    if improved and evolve.git("diff", "origin/main..HEAD", "--",
                               str(evolve.PROMPT_FILE.name)):
        print("!!! WARNING: prompt improvement did not land on origin/main")
    print(f">>> report written: {pdf}")
    return str(pdf)


if __name__ == "__main__":
    stop_at = sys.argv[1] if len(sys.argv) > 1 else "18:00"
    stop = datetime.now().replace(hour=int(stop_at[:2]), minute=int(stop_at[3:5]),
                                  second=0, microsecond=0)
    print(f"Running cycles until {stop}. Improve pass every {IMPROVE_INTERVAL // 60} min.")
    history = evolve.load_history()
    last_improve = time.time()
    failures = 0
    while datetime.now() < stop:
        cycle_no = len(history) + 1
        try:
            evolve.run_cycle(cycle_no, history, improve=False)
            failures = 0
        except Exception as e:
            failures += 1
            print(f"!!! cycle {cycle_no} CRASHED: {type(e).__name__}: {e}")
            evolve.git("checkout", "--", "interviewer_prompt.txt")
            if failures >= 3:
                print("!!! three consecutive failures — stopping cycles early.")
                break
        if time.time() - last_improve >= IMPROVE_INTERVAL:
            try:
                improve_pass(history)
            except Exception as e:
                print(f"!!! improve pass failed: {type(e).__name__}: {e}")
                evolve.git("checkout", "--", "interviewer_prompt.txt")
            last_improve = time.time()
        time.sleep(2)

    print("\n=== stop time reached: final improve pass ===")
    try:
        improve_pass(history)
    except Exception as e:
        print(f"!!! final improve pass failed: {type(e).__name__}: {e}")
    print("DONE.")
