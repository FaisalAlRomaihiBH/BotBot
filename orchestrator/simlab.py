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


GENERATOR_MODEL = "claude-sonnet-5"
ANALYST_MODEL = "claude-opus-5"

GENERATOR_PROMPT = """Generate {count} MAXIMALLY DIFFERENT fake business-owner
personas for testing a chatbot-requirements interviewer. Vary aggressively:
industry (food, trades, professional services, retail, beauty, automotive,
events, manufacturing, health...), company size (solo up to ~30 staff),
personality (patient/impatient, chatty/terse, tech-savvy/technophobe,
suspicious/trusting), and communication style (some mix Arabic or Spanish
words in). Each persona must include concrete facts: services with real
prices, hours, team, location, a reason they want a chatbot, a rough budget.

Output ONLY a JSON array, each element:
{{"name": "<short persona name>", "industry": "<industry>",
  "company_size": "<e.g. solo / 3 staff / 12 staff>",
  "traits": "<personality + communication style, one line>",
  "profile": "<8-14 sentences: the full identity, facts, and voice the
              roleplayer will use>"}}"""

ANALYST_PROMPT = """You are an expert conversation designer and product
analyst. Below: the CURRENT interviewer system prompt, the CURRENT output
schema (Pydantic source), and {n} complete test cases — for each, a fake
owner's identity, the full interview transcript (INPUT), and the final
requirements brief (OUTPUT).

Produce a rigorous markdown report:

# Communication problems (from the transcripts)
For each real problem found: the evidence (quote the exchange, name the
case), why it hurts the interview, and the fix — including the EXACT
sentence(s) to add/change in the interviewer prompt, as a ready-to-apply
draft in a fenced block.

# Schema gaps (from the briefs)
For each kind of business where the brief lost or had no home for
information the downstream Architect Bot would need to design a good
chatbot: the evidence, the impact, and the fix — the EXACT proposed Pydantic
field(s) with types and a comment, as a ready-to-apply draft in a fenced
block.

# Top 5 actions
The five changes with the highest payoff, ranked, one line each.

Only report REAL problems with evidence — an empty section with "no issues
found" is a valid finding. Never invent problems to fill space."""


def start_test(kind: str, params: dict) -> dict:
    if kind != "persona_sweep":
        return {"error": f"unknown test kind {kind}"}
    count = max(2, min(8, int(params.get("count") or 4)))
    test_id = store.sim_create_test(kind, {"count": count})
    threading.Thread(target=_run_sweep, args=(test_id, count),
                     daemon=True).start()
    return {"test_id": test_id}


def _usage_of(reply) -> dict:
    u = reply.response_metadata.get("usage") or {}
    return {"fresh_in": u.get("input_tokens") or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or 0,
            "out": u.get("output_tokens") or 0}


def _cost_of(model: str, u: dict) -> float:
    return store._cost_usd(model, u["fresh_in"], u["cache_read"],
                           u["cache_write"], u["out"]) or 0.0


def _run_sweep(test_id: int, count: int) -> None:
    from concurrent.futures import ThreadPoolExecutor
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage
        from requirements_bot import _blocks_to_text

        # 1) generate the fake identities (registered with industry/size/traits)
        store.sim_update_test(test_id, status="generating")
        gen = ChatAnthropic(model=GENERATOR_MODEL, max_tokens=8000)
        reply = gen.invoke([HumanMessage(
            content=GENERATOR_PROMPT.replace("{count}", str(count)))])
        g_usage = _usage_of(reply)
        text = _blocks_to_text(reply.content)
        text = text[text.index("["):text.rindex("]") + 1]
        personas = json.loads(text)[:count]
        persona_ids = [store.sim_add_persona(
            p.get("name", f"Persona {i+1}"), "ai", p.get("profile", ""),
            industry=p.get("industry"), company_size=p.get("company_size"),
            traits=p.get("traits")) for i, p in enumerate(personas)]

        # 2) run every interview (in parallel, live-archived like any run)
        store.sim_update_test(test_id, status="running")
        run_ids = [store.sim_create_run(pid) for pid in persona_ids]
        store.sim_update_test(test_id, run_ids=run_ids)
        with ThreadPoolExecutor(max_workers=count) as ex:
            list(ex.map(lambda rp: _execute(rp[0], store.sim_persona(rp[1])),
                        zip(run_ids, persona_ids)))

        # 3) the analyst reads every input and output together
        store.sim_update_test(test_id, status="analyzing")
        root = Path(__file__).parent.parent
        prompt_text = (root / "interviewer_prompt.txt").read_text(encoding="utf-8")
        schema_text = (root / "models.py").read_text(encoding="utf-8")
        cases = []
        for rid in run_ids:
            r = store.sim_run(rid)
            convo = "\n".join(
                f"{'BOT' if t['who'] == 'bot' else 'OWNER'}: {t['text']}"
                for t in (r["transcript"] or []))
            brief = json.dumps(r["brief"], ensure_ascii=False) if r["brief"] else "(none)"
            p = store.sim_persona(r["persona_id"]) or {}
            cases.append(
                f"=== CASE {rid}: {r['persona_name']} — {p.get('industry')} — "
                f"{p.get('company_size')} — {p.get('traits')} ===\n"
                f"--- TRANSCRIPT (INPUT) ---\n{convo}\n"
                f"--- FINAL BRIEF (OUTPUT) ---\n{brief}\n")
        analyst = ChatAnthropic(model=ANALYST_MODEL, max_tokens=16000)
        a_reply = analyst.invoke([HumanMessage(content=(
            ANALYST_PROMPT.replace("{n}", str(len(cases)))
            + f"\n\n=== CURRENT INTERVIEWER PROMPT ===\n{prompt_text}\n"
            + f"\n=== CURRENT OUTPUT SCHEMA (models.py) ===\n{schema_text}\n\n"
            + "\n".join(cases)))])
        a_usage = _usage_of(a_reply)
        report = _blocks_to_text(a_reply.content)

        runs_cost = sum((store.sim_run(rid) or {}).get("cost_usd") or 0
                        for rid in run_ids)
        total = (runs_cost + _cost_of(GENERATOR_MODEL, g_usage)
                 + _cost_of(ANALYST_MODEL, a_usage))
        store.sim_update_test(
            test_id, status="completed", report=report,
            usage={"generator": g_usage, "analyst": a_usage},
            cost_usd=round(total, 4), finished_ts=time.time())
    except Exception as e:
        store.sim_update_test(test_id, status="failed",
                              error=f"{type(e).__name__}: {e}",
                              finished_ts=time.time())


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
