# evolve_until.py — run evolve cycles back-to-back until a stop time (default 18:00
# today). Cycles only REGISTER problems; once per hour an IMPROVE pass fixes the
# prompt from everything registered since the last pass and writes a PDF report
# (problems -> fixes) into evolve_runs/reports/ for emailing.
#
# Usage: python evolve_until.py [HH:MM]
import difflib
import json
import sys
import time
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle)

import evolve

REPORTS_DIR = evolve.RUNS_DIR / "reports"
POINTER_FILE = evolve.RUNS_DIR / "improve_pointer.json"  # how many history entries are already fixed
IMPROVE_INTERVAL = 3600  # seconds between improve passes


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_persona(persona: str, limit=90) -> str:
    """'industry / long personality essay' -> 'industry — first clause of trait'."""
    industry, _, trait = persona.partition(" / ")
    for stop in (" — ", ". ", "; ", ", "):
        if stop in trait:
            trait = trait.split(stop)[0]
            break
    label = industry.strip() + (f" — {trait.strip()}" if trait.strip() else "")
    return label[:limit].rstrip() + ("…" if len(label) > limit else "")


def prompt_diff(old: str, new: str, max_lines=60) -> list[str]:
    lines = [l.rstrip() for l in difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=1)][2:]  # drop ---/+++
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more diff lines)"]
    return lines


def write_pdf(path, batch, improved_desc, old_prompt, new_prompt):
    styles = getSampleStyleSheet()
    h1, h2, body = styles["Title"], styles["Heading2"], styles["BodyText"]
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], spaceBefore=10)
    small = ParagraphStyle("small", parent=body, fontSize=9, leading=12)
    quote = ParagraphStyle("quote", parent=small, leftIndent=18,
                           textColor="#555555", fontSize=8, leading=11)
    mono = ParagraphStyle("mono", parent=small, fontName="Courier",
                          fontSize=7.5, leading=9.5)
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm)

    def para(text, style):
        # one Paragraph per source line: huge multi-line paragraphs are what
        # made ReportLab garble and overlap text in earlier reports
        return [Paragraph(esc(line) if line.strip() else "&nbsp;", style)
                for line in text.splitlines()]

    avg = round(sum(h["score"] for h in batch) / len(batch), 2)
    story = [Paragraph("BotBot Evolve — Hourly Report", h1),
             Paragraph(datetime.now().strftime("%A %d %B %Y, %H:%M"), body),
             Spacer(1, 10)]

    # ---- at a glance ----
    story.append(Paragraph(
        f"<b>{len(batch)}</b> interview(s) · average score <b>{avg}/10</b> · "
        + ("<b>prompt updated</b> this hour"
           if improved_desc else "no prompt change this hour"), body))
    rows = [["Cycle", "Persona", "Score", "Done"]]
    for h in batch:
        rows.append([str(h["cycle"]),
                     Paragraph(esc(short_persona(h["persona"])), small),
                     f"{h['score']}/10", "yes" if h["completed"] else "NO"])
    table = Table(rows, colWidths=[1.5 * cm, 11 * cm, 2 * cm, 1.5 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b3a55")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8cdd6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [Spacer(1, 6), table]

    # ---- problems, one section per interview ----
    story.append(Paragraph("Problems found", h2))
    for h in batch:
        story.append(Paragraph(
            f"Cycle {h['cycle']} — {esc(short_persona(h['persona']))} "
            f"({h['score']}/10)", h3))
        for i, f in enumerate(h.get("findings", []), 1):
            problem = f if isinstance(f, str) else f["problem"]
            story.append(Paragraph(f"<b>{i}.</b> {esc(problem)}", small))
            excerpt = "" if isinstance(f, str) else f.get("excerpt", "")
            for line in excerpt.splitlines():
                if line.strip():
                    story.append(Paragraph(f"<i>{esc(line.strip())}</i>", quote))
        story.append(Paragraph(
            f"<b>Top suggested fix:</b> {esc(h['top_improvement'])}", small))
        for s in h.get("schema_suggestions", []):
            story.append(Paragraph(
                f"<b>⚠ Needs human/code change:</b> {esc(s)}", small))

    # ---- what changed, as a diff instead of the whole prompt ----
    story.append(Paragraph("Prompt change this hour", h2))
    if improved_desc:
        story.append(Paragraph(esc(improved_desc), body))
        story.append(Paragraph("Diff against the previous prompt "
                               "(- removed, + added):", small))
        for line in prompt_diff(old_prompt, new_prompt):
            color = ("#1a7f37" if line.startswith("+")
                     else "#b42318" if line.startswith("-") else "#555555")
            story.append(Paragraph(
                f'<font color="{color}">{esc(line) or "&nbsp;"}</font>', mono))
    else:
        story.append(Paragraph(
            "No prompt change (improvement rejected by guardrails or produced "
            "no change). The full current prompt lives in "
            "interviewer_prompt.txt in the repo.", body))
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
