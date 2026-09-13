# simlab.py — the Simulation Lab: fake business-owner personas interview
# against the real RequirementsBot, and every run is archived permanently
# (persona, full transcript, final brief, token usage, cost) so any test can
# be inspected and re-run later against the improved bot.
#
# Two persona kinds:
#   scripted — a fixed list of owner lines, replayed in order (deterministic,
#              perfect for regression comparisons)
#   ai       — a persona profile roleplayed live by a cheap model
#              (claude-haiku-4-5), so it actually answers whatever the bot
#              asks, in character
#
# Lab runs are fully separate from production: no project, no Client ID, no
# workflow card, nothing in the Terminal — the archive lives in sim_runs.
#
# Cost hygiene (standing rule): the bot side already carries the full kit
# (delta output, adaptive cache, compact schema); the persona side runs on
# the cheapest model with its stable system prompt behind a cache breakpoint
# and the conversation prefix cached with a moving breakpoint; both sides'
# usage is recorded per run.
import json
import tempfile
import threading
import time
from pathlib import Path

from orchestrator import store

MAX_BOT_TURNS = 45          # hard stop: no interview runs forever
NUDGE = ("That's really everything I can tell you. Please finish and close "
         "this up now. Confirmed.")

PERSONA_MODEL = "claude-haiku-4-5"
PERSONA_SYSTEM = """You are roleplaying a BUSINESS OWNER being interviewed by a
chatbot-requirements consultant. Stay in character at all times.

YOUR PROFILE (facts, personality, and how you communicate):
{profile}

Rules:
- Answer the interviewer's LAST question concretely, with real details drawn
  from (or consistent with) your profile. Invent plausible specifics when the
  profile is silent, and keep them consistent for the whole conversation.
- Reply the way this owner would text: usually 1-3 sentences, in their voice.
- If asked to share chat exports or files, say you have none handy.
- When the interviewer summarizes everything and asks you to confirm, confirm
  if it is accurate (correct it briefly if not).
- After you have confirmed the summary once, your next reply is only a short
  goodbye like "Confirmed, thanks, goodbye."
- Never break character, never mention being an AI, output only the owner's
  message text."""


def start_run(persona_id: int) -> dict:
    persona = store.sim_persona(persona_id)
    if not persona:
        return {"error": f"unknown persona {persona_id}"}
    run_id = store.sim_create_run(persona_id)
    t = threading.Thread(target=_execute, args=(run_id, persona), daemon=True)
    t.start()
    return {"run_id": run_id}


def _persona_reply(llm, profile: str, transcript: list) -> tuple[str, dict]:
    """The persona's next message, roleplayed by the cheap model."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    cached = {"cache_control": {"type": "ephemeral"}}
    system = PERSONA_SYSTEM.replace("{profile}", profile)
    msgs = [SystemMessage(content=[{"type": "text", "text": system, **cached}])]
    # from the persona's seat: bot lines are the human speaking to it
    turns = [t for t in transcript]
    for i, t in enumerate(turns):
        content = ([{"type": "text", "text": t["text"], **cached}]
                   if i == len(turns) - 1 else t["text"])
        cls = HumanMessage if t["who"] == "bot" else AIMessage
        msgs.append(cls(content=content))
    reply = llm.invoke(msgs)
    u = reply.response_metadata.get("usage") or {}
    usage = {"fresh_in": u.get("input_tokens") or 0,
             "cache_read": u.get("cache_read_input_tokens") or 0,
             "cache_write": u.get("cache_creation_input_tokens") or 0,
             "out": u.get("output_tokens") or 0}
    from requirements_bot import _blocks_to_text
    return _blocks_to_text(reply.content).strip(), usage


def _acc(total: dict, u: dict) -> None:
    for k in total:
        total[k] += u.get(k, 0)


def _execute(run_id: int, persona: dict) -> None:
    transcript: list[dict] = []
    p_usage = {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}
    try:
        from requirements_bot import RequirementsBot
        # isolated empty uploads dir: lab runs never touch real materials
        updir = Path(tempfile.mkdtemp(prefix="simlab_"))
        bot = RequirementsBot(uploads_dir=updir)
        transcript.append({"who": "bot", "text": RequirementsBot.GREETING})

        scripted = persona["kind"] == "scripted"
        if scripted:
            lines = [ln.strip() for ln in persona["content"].splitlines()
                     if ln.strip()]
            lines += [NUDGE] * 3
        else:
            from langchain_anthropic import ChatAnthropic
            persona_llm = ChatAnthropic(model=PERSONA_MODEL, max_tokens=400)

        turn = None
        for i in range(MAX_BOT_TURNS):
            if scripted:
                if i >= len(lines):
                    break
                owner_msg = lines[i]
            else:
                owner_msg, pu = _persona_reply(
                    persona_llm, persona["content"], transcript)
                _acc(p_usage, pu)
                if not owner_msg:
                    break
            transcript.append({"who": "persona", "text": owner_msg})
            msgs, turn = bot.send(owner_msg)
            for m in msgs:
                transcript.append({"who": "bot", "text": m})
            # live archive: the UI can watch the run as it happens
            store.sim_update_run(run_id, transcript=transcript)
            if bot.complete:
                break

        cost = (store._cost_usd("claude-sonnet-5", bot.usage["fresh_in"],
                                bot.usage["cache_read"], bot.usage["cache_write"],
                                bot.usage["out"]) or 0.0)
        cost += (store._cost_usd(PERSONA_MODEL, p_usage["fresh_in"],
                                 p_usage["cache_read"], p_usage["cache_write"],
                                 p_usage["out"]) or 0.0)
        store.sim_update_run(
            run_id, status="completed",
            interview_complete=int(bot.complete),
            transcript=transcript,
            brief=turn.requirements.model_dump() if turn else None,
            usage={"bot": bot.usage, "persona": p_usage},
            cost_usd=round(cost, 4), finished_ts=time.time())
    except Exception as e:
        store.sim_update_run(
            run_id, status="failed", transcript=transcript,
            error=f"{type(e).__name__}: {e}", finished_ts=time.time())
