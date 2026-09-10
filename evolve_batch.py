# evolve_batch.py — one bounded batch of the evolve loop, for cloud routine runs.
# Runs N register-only interview cycles, then one improve pass (prompt fix + PDF
# report into evolve_runs/reports/), committing and pushing everything.
#
# Several sibling routines run this concurrently against the same repo, so every
# push goes through evolve.sync_push, which rebases onto siblings' pushes and
# merges the shared history instead of losing either side's work.
#
# Usage: python evolve_batch.py [cycles] (--full | --register-only)
#   --full:          interview cycles + the single hourly improve pass + PDF.
#   --register-only: interview cycles only (alias: --no-improve); a designated
#                    sibling routine runs the improve pass, so the prompt is
#                    rewritten once per hour instead of once per routine
#                    (repeated rewrites are what bloated it).
#   Neither flag:    exits immediately. The original six staggered routines all
#                    invoke this script bare; after consolidating to two
#                    routines this retires them without editing their triggers.
import sys

import evolve
import evolve_until

if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not flags & {"--full", "--register-only", "--no-improve"}:
        print("retired invocation: this routine has been consolidated away. "
              "The two remaining routines call --full or --register-only. "
              "Nothing to do; exiting.")
        sys.exit(0)
    do_improve = "--full" in flags
    n = int(args[0]) if args else 3
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
    if do_improve:
        try:
            evolve_until.improve_pass(history)  # commits + syncs + writes the PDF
        except Exception as e:
            print(f"!!! improve pass failed: {type(e).__name__}: {e}")
            evolve.git("checkout", "--", "interviewer_prompt.txt")
    else:
        print("register-only batch: improve pass left to the designated routine.")
    print("BATCH DONE.")
