# tests/simulate.py — a full offline dress rehearsal of the evolve system.
# Runs the REAL pipeline (persona -> interview -> evaluate -> register ->
# hourly improve pass -> PDF) with a fake LLM in a sandbox: zero API cost,
# zero git commits, the live loop and real files untouched.
#
# Usage:  python -m tests.simulate [cycles]      (default 3)
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import intake
import evolve
import evolve_until
from tests import fakes


def main(n_cycles=3):
    tmp = Path(tempfile.mkdtemp(prefix="evolve_sim_"))
    runs = tmp / "evolve_runs"
    runs.mkdir()
    prompt = tmp / "interviewer_prompt.txt"
    prompt.write_text("You are Birdie. Interview the owner warmly. " * 20
                      + "\n{analysis}\n{format_instructions}\n", encoding="utf-8")

    # sandbox: fake model, no-op git, temp paths
    evolve.ROOT, evolve.RUNS_DIR = tmp, runs
    evolve.HISTORY_FILE, evolve.PROMPT_FILE = runs / "history.json", prompt
    evolve_until.REPORTS_DIR = runs / "reports"
    evolve_until.POINTER_FILE = runs / "improve_pointer.json"
    evolve.git = lambda *a: "(git skipped in simulation)"
    evolve.llm = evolve.strong_llm = fakes.FakeLLM()
    intake.ask = fakes.FakeAsk()
    intake.analyze_uploads = lambda: (fakes.FAKE_ANALYSIS, ["whatsapp_export.txt"])

    print(f"SIMULATION sandbox: {tmp}\n")
    print(f"--- phase 1: {n_cycles} register-only cycles (like evolve_until does) ---")
    history = []
    for i in range(1, n_cycles + 1):
        intake.ask = fakes.FakeAsk()  # fresh script per interview
        evolve.run_cycle(i, history, improve=False)

    print("\n--- phase 2: hourly improve pass (batch fix + PDF report) ---")
    pdf = evolve_until.improve_pass(history)

    print("\n--- results ---")
    print(f"history entries: {len(history)}")
    for h in history:
        print(f"  cycle {h['cycle']}: score {h['score']}/10, "
              f"{len(h['findings'])} findings (each with a chat excerpt)")
    print(f"prompt evolved: {'payment methods' in prompt.read_text(encoding='utf-8')}")
    print(f"pointer: {json.loads(evolve_until.POINTER_FILE.read_text())}")
    print(f"PDF report: {pdf}")
    print("\nsecond pass with nothing new registered:",
          evolve_until.improve_pass(history) or "correctly skipped")
    print(f"\nAll artifacts kept for inspection under: {tmp}")
    return pdf


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
