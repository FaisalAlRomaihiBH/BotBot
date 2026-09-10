# evolve_batch.py — one bounded batch of the evolve loop, for cloud routine runs.
#
# Usage: python evolve_batch.py [cycles] [--full | --register-only]
#   --full:          interview cycles + the improve pass (prompt rewrite + PDF).
#   --register-only: interview cycles only.
#   No flag (how the six original staggered routines invoke it): the
#   consolidation is enforced HERE, since those routines' triggers cannot be
#   edited from a session. Each bare run registers interviews only while a
#   shared hourly quota (read from origin/main's commit history) allows, and
#   runs the improve pass only if no improve has landed in the last 50
#   minutes — so however many routines fire, the hour gets at most
#   INTERVIEW_QUOTA interviews and ONE prompt rewrite.
import sys
import time

import evolve
import evolve_until

INTERVIEW_QUOTA = 6         # max interviews registered per rolling hour
IMPROVE_GAP_S = 50 * 60     # min seconds between improve passes


def cycles_last_hour() -> int:
    evolve.git("fetch", "origin", "main")
    out = evolve.git("log", "--since=60 minutes ago", "--format=%s", "origin/main")
    return sum(1 for line in out.splitlines()
               if line.startswith("evolve cycle ") and "crashed" not in line)


def improve_due() -> bool:
    evolve.git("fetch", "origin", "main")
    ts = evolve.git("log", "-1", "--format=%ct",
                    "--grep", "evolve hourly improve", "origin/main")
    try:
        return (time.time() - int(ts.strip())) >= IMPROVE_GAP_S
    except ValueError:
        return True  # no improve commit yet


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    legacy = not flags & {"--full", "--register-only", "--no-improve"}
    n = int(args[0]) if args else 3

    history = evolve.load_history()
    failures = 0
    for _ in range(n):
        if legacy and cycles_last_hour() >= INTERVIEW_QUOTA:
            print(f"hourly quota of {INTERVIEW_QUOTA} interviews already "
                  "registered on origin/main — skipping remaining cycles.")
            break
        try:
            evolve.run_cycle(len(history) + 1, history, improve=False)
            failures = 0
        except Exception as e:
            failures += 1
            print(f"!!! cycle CRASHED: {type(e).__name__}: {e}")
            evolve.git("checkout", "--", "interviewer_prompt.txt")
            evolve.git("add", "-A")  # persona + error.log of the failed cycle
            evolve.git("commit", "-m",
                       f"evolve cycle crashed: {type(e).__name__}\n\n"
                       "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>")
            if failures >= 2:
                print("!!! two consecutive failures — stopping the batch.")
                evolve.sync_push(history)
                break
        # sync after every cycle: publishes results early and rebases us onto
        # whatever sibling routines pushed meanwhile
        evolve.sync_push(history)

    do_improve = "--full" in flags or (legacy and improve_due())
    if do_improve:
        try:
            evolve_until.improve_pass(history)  # commits + syncs + writes the PDF
        except Exception as e:
            print(f"!!! improve pass failed: {type(e).__name__}: {e}")
            evolve.git("checkout", "--", "interviewer_prompt.txt")
    else:
        print("improve pass skipped: done recently by a sibling routine "
              "(or --register-only).")
    print("BATCH DONE.")
