# persona_test.py — manual persona testing for RequirementsBot.
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

from requirements_bot import RequirementsBot

ROOT = Path(__file__).parent
SESSIONS_DIR = ROOT / "persona_sessions"


class PersonaSession:
    """One persisted interview: a RequirementsBot plus its transcript on disk."""

    def __init__(self, name: str, state: dict):
        self.name = name
        self.transcript: list[list[str]] = state["transcript"]
        self.form: dict | None = state["form"]
        self.brief: dict | None = state["brief"]
        self.bot = RequirementsBot.from_dict(state["bot"])

    # ---------------- persistence ----------------
    @staticmethod
    def _path(name: str) -> Path:
        return SESSIONS_DIR / f"{name}.json"

    @classmethod
    def create(cls, name: str) -> "PersonaSession":
        session = cls(name, {
            "bot": RequirementsBot().to_dict(),
            "transcript": [["Bot", RequirementsBot.GREETING]],
            "form": None,
            "brief": None,
        })
        session.save()
        return session

    @classmethod
    def load(cls, name: str) -> "PersonaSession":
        path = cls._path(name)
        if not path.exists():
            sys.exit(f"no session named '{name}' — start one with: "
                     f"python persona_test.py new {name}")
        return cls(name, json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        SESSIONS_DIR.mkdir(exist_ok=True)
        self._path(self.name).write_text(
            json.dumps({
                "bot": self.bot.to_dict(),
                "transcript": self.transcript,
                "form": self.form,
                "brief": self.brief,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8")

    # ---------------- one conversational turn ----------------
    def say(self, message: str) -> None:
        if self.bot.complete:
            sys.exit("this interview is already complete — see: "
                     f"python persona_test.py form {self.name}")

        messages, turn = self.bot.send(message)
        self.transcript.append(["Owner", message])
        for msg in messages:
            self.transcript.append(["Bot", msg])
            print("Bot:", msg)

        self.form = turn.requirements.model_dump()
        if self.bot.complete:
            self.brief = self.bot.brief(turn)
            print("\n[COMPLETE] final brief:")
            print(json.dumps(self.form, indent=2, ensure_ascii=False))
        self.save()


def cmd_list() -> None:
    if not SESSIONS_DIR.exists():
        print("(no sessions)")
        return
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        state = json.loads(path.read_text(encoding="utf-8"))
        status = "complete" if state["bot"]["complete"] else "in progress"
        turns = len(state["transcript"])
        print(f"{path.stem:20s} {status:12s} {turns} transcript entries")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__ or "see file header for usage")
    cmd = args[0]
    if cmd == "new" and len(args) == 2:
        PersonaSession.create(args[1])
        print("Bot:", RequirementsBot.GREETING)
    elif cmd == "say" and len(args) == 3:
        PersonaSession.load(args[1]).say(args[2])
    elif cmd == "transcript" and len(args) == 2:
        for who, msg in PersonaSession.load(args[1]).transcript:
            print(f"{who}: {msg}\n")
    elif cmd == "form" and len(args) == 2:
        print(json.dumps(PersonaSession.load(args[1]).form, indent=2, ensure_ascii=False))
    elif cmd == "list" and len(args) == 1:
        cmd_list()
    else:
        sys.exit("usage: python persona_test.py new|say|transcript|form|list "
                 "<session> [message]")
