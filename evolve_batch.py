# evolve_batch.py — one bounded batch of the evolve loop, for cloud routine runs.
# Runs N register-only interview cycles, then one improve pass (prompt fix + PDF
# report into evolve_runs/reports/), committing and pushing everything.
#
# Several sibling routines run this concurrently against the same repo, so every
# push goes through evolve.sync_push, which rebases onto siblings' pushes and
# merges the shared history instead of losing either side's work.
#
# Usage: python evolve_batch.py [cycles]     (default 3)
import sys

import evolve
import evolve_until

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    history = evolve.load_history()
    failures = 0
    for _ in range(n):
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
    try:
        evolve_until.improve_pass(history)  # commits + syncs + writes the PDF
    except Exception as e:
        print(f"!!! improve pass failed: {type(e).__name__}: {e}")
        evolve.git("checkout", "--", "interviewer_prompt.txt")
    print("BATCH DONE.")
