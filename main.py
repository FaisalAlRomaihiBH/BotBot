# main.py — interactive chat with RequirementsBot.
#
# Run:  python main.py
# Type your answers; put chat exports / screenshots into uploads/ when asked
# and tell the bot when they're ready. When the interview completes, the full
# brief is saved to requirements_brief.json.
import json
import sys

from requirements_bot import RequirementsBot

if __name__ == "__main__":
    # Windows consoles often default to cp1252; the model freely uses emoji
    # and accented text, and one print() must never crash the interview.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    bot = RequirementsBot()
    bot.uploads_dir.mkdir(exist_ok=True)  # so the folder exists when the bot mentions it
    print("Bot:", RequirementsBot.GREETING)

    turn = None  # latest turn, so an early exit can still save a partial brief
    while True:
        try:
            user_msg = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_msg:
            continue
        if user_msg.lower() in ("quit", "exit"):
            break

        messages, turn = bot.send(user_msg)
        for msg in messages:
            print("\nBot:", msg)

        if bot.complete:
            with open("requirements_brief.json", "w", encoding="utf-8") as f:
                json.dump(bot.brief(turn), f, indent=2, ensure_ascii=False)
            print("\n[Saved the full brief to requirements_brief.json]")
            break

    # The owner quit before confirming: don't lose what was gathered.
    if not bot.complete and turn is not None:
        brief = bot.brief(turn)
        brief["partial"] = True
        with open("requirements_brief.json", "w", encoding="utf-8") as f:
            json.dump(brief, f, indent=2, ensure_ascii=False)
        print("\n[Interview unfinished — saved a PARTIAL brief to requirements_brief.json]")

    print("\nGoodbye!")
