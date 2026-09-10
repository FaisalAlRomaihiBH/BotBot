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


def write_pdf(path, batch, improved_desc, old_prompt, new_prompt):
    styles = getSampleStyleSheet()
    h1, h2, body = styles["Title"], styles["Heading2"], styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=9, leading=12)
    quote = ParagraphStyle("quote", parent=small, leftIndent=18,
                           textColor="#555555", fontSize=8, leading=11)
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)
    story = [Paragraph("BotBot Evolve — Hourly Report", h1),
             Paragraph(datetime.now().strftime("%A %d %B %Y, %H:%M"), body),
             Spacer(1, 12)]

    story.append(Paragraph(f"Interviews in this batch: {len(batch)}", h2))
    for h in batch:
        story.append(Paragraph(
            f"<b>Cycle {h['cycle']}</b> — {esc(h['persona'])} — score {h['score']}/10"
            + ("" if h["completed"] else " — <b>DID NOT COMPLETE</b>"), body))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Problems found", h2))
    for h in batch:
        story.append(Paragraph(f"<b>Cycle {h['cycle']} ({esc(h['persona'])})</b>", body))
        for f in h.get("findings", []):
            if isinstance(f, str):
                story.append(Paragraph(f"• {esc(f)}", small))
                continue
            story.append(Paragraph(f"• {esc(f['problem'])}", small))
            if f.get("excerpt"):
                story.append(Paragraph(
                    "<i>" + esc(f["excerpt"]).replace("\n", "<br/>") + "</i>", quote))
        story.append(Paragraph(f"<i>Top suggested fix: {esc(h['top_improvement'])}</i>", small))
        if h.get("schema_suggestions"):
            for s in h["schema_suggestions"]:
                story.append(Paragraph(f"⚠ Needs human/code change: {esc(s)}", small))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 8))
    story.append(Paragraph("Fix applied", h2))
    if improved_desc:
        story.append(Paragraph(esc(improved_desc), body))
        story.append(Paragraph("New interviewer prompt now in effect:", body))
        story.append(Paragraph(esc(new_prompt).replace("\n", "<br/>"), small))
    else:
        story.append(Paragraph(
            "No prompt change this hour (improvement rejected by guardrails "
            "or produced no change). The prompt below remains in effect.", body))
        story.append(Paragraph(esc(old_prompt).replace("\n", "<br/>"), small))
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
