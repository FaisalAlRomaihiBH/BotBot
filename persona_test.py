# persona_test.py — manual persona testing for the requirements bot (intake.py).
#
# A human (or an agent) role-plays a business-owner persona and talks to the
# bot one message at a time. Each session keeps its full state on disk, so a
# conversation can span many separate command invocations — which is exactly
# how an agent drives it.
#
# Usage:
#   python persona_test.py new <session>              start a session, print the greeting
#   python persona_test.py say <session> "<message>"  send one owner message, print the reply
#   python persona_test.py transcript <session>       print the whole conversation so far
#   python persona_test.py form <session>             print the latest requirements form
#   python persona_test.py list                       list sessions
#
# To test file analysis: put chat exports / screenshots into uploads/ before
# telling the bot the files are ready — it decides when to run the analysis.
# When the bot marks the interview complete, the final brief is printed and
# saved into the session file under "brief".
import json
import sys
from pathlib import Path

import intake

ROOT = Path(__file__).parent
SESSIONS_DIR = ROOT / "persona_sessions"

GREETING = ("Hi! I help businesses figure out exactly what they need from a "
            "chatbot. Before we start — what's your name?")

ANALYSIS_DONE_NOTE = (
    "(System note: the analysis of the shared materials is now in your "
    "instructions. React to it: mention 1-2 useful things you learned, "
    "then ask about the first knowledge gap.)")
ANALYSIS_EMPTY_NOTE = (
    "(System note: the uploads folder is empty — no readable files. "
    "Gently tell the owner nothing was found and how to add files, "
    "then continue the interview.)")


def _path(name: str) -> Path:
    return SESSIONS_DIR / f"{name}.json"


def _load(name: str) -> dict:
    p = _path(name)
    if not p.exists():
        sys.exit(f"no session named '{name}' — start one with: "
                 f"python persona_test.py new {name}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(name: str, state: dict) -> None:
    SESSIONS_DIR.mkdir(exist_ok=True)
    _path(name).write_text(json.dumps(state, indent=2, ensure_ascii=False),
                           encoding="utf-8")


def cmd_new(name: str) -> None:
    _save(name, {
        "history": [["ai", GREETING]],
        "transcript": [["Bot", GREETING]],
        "analysis_text": intake.NO_MATERIALS,
        "analysis": None,
        "form": None,
        "brief": None,
        "complete": False,
    })
    print("Bot:", GREETING)


def cmd_say(name: str, message: str) -> None:
    state = _load(name)
    if state["complete"]:
        sys.exit("this interview is already complete — see: "
                 f"python persona_test.py form {name}")
    history = [tuple(pair) for pair in state["history"]]

    turn, raw = intake.ask(message, history, state["analysis_text"])
    history += [("human", message), ("ai", raw)]
    state["transcript"] += [["Owner", message], ["Bot", turn.next_message]]
    print("Bot:", turn.next_message)

    # The bot decided the owner's files are ready: analyze, then let it react.
    if turn.run_file_analysis:
        analysis, filenames = intake.analyze_uploads()
        if analysis is None:
            note = ANALYSIS_EMPTY_NOTE
        else:
            state["analysis"] = analysis.model_dump()
            state["analysis_text"] = intake.analysis_to_prompt_text(analysis)
            note = ANALYSIS_DONE_NOTE
            print(f"[analyzed {len(filenames)} file(s): {', '.join(filenames)}]")
        turn, raw = intake.ask(note, history, state["analysis_text"])
        history += [("human", "(files were analyzed)"), ("ai", raw)]
        state["transcript"] += [["Bot", turn.next_message]]
        print("Bot:", turn.next_message)

    state["history"] = [list(pair) for pair in history]
    state["form"] = turn.requirements.model_dump()
    if turn.interview_complete:
        state["complete"] = True
        state["brief"] = {
            "requirements": state["form"],
            "conversation_analysis": state["analysis"],
        }
        print("\n[COMPLETE] final brief:")
        print(json.dumps(state["form"], indent=2, ensure_ascii=False))
    _save(name, state)


def cmd_transcript(name: str) -> None:
    for who, msg in _load(name)["transcript"]:
        print(f"{who}: {msg}\n")


def cmd_form(name: str) -> None:
    print(json.dumps(_load(name)["form"], indent=2, ensure_ascii=False))


def cmd_list() -> None:
    if not SESSIONS_DIR.exists():
        print("(no sessions)")
        return
    for p in sorted(SESSIONS_DIR.glob("*.json")):
        state = json.loads(p.read_text(encoding="utf-8"))
        status = "complete" if state["complete"] else "in progress"
        turns = len(state["transcript"])
        print(f"{p.stem:20s} {status:12s} {turns} transcript entries")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__ or "see file header for usage")
    cmd = args[0]
    if cmd == "new" and len(args) == 2:
        cmd_new(args[1])
    elif cmd == "say" and len(args) == 3:
        cmd_say(args[1], args[2])
    elif cmd == "transcript" and len(args) == 2:
        cmd_transcript(args[1])
    elif cmd == "form" and len(args) == 2:
        cmd_form(args[1])
    elif cmd == "list" and len(args) == 1:
        cmd_list()
    else:
        sys.exit("usage: python persona_test.py new|say|transcript|form|list "
                 "<session> [message]")
