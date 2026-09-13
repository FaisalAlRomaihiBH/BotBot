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

# The completeness ladder: how much of their own business each persona in a
# batch actually knows. Spread across the batch so every sweep tests both
# owners who know everything and owners who barely know anything.
COMPLETENESS_LADDER = [1.0, 0.85, 0.7, 0.55, 0.4, 0.25, 0.9, 0.6]
# ...and independently, how much conversation each persona tolerates: 1.0
# chats happily and answers richly; 0.15 wants it over in a handful of
# messages and starts pushing back. Interleaved so knowledge and patience
# combinations vary across a batch (a knowledgeable-but-impatient owner is
# a different test than a clueless-but-chatty one).
PATIENCE_LADDER = [0.9, 0.3, 0.6, 0.15, 1.0, 0.45, 0.75, 0.2]

PATIENCE_RULE = """
PATIENCE LEVEL: {pct}% —
- 80-100%: happy to talk, gives long detailed answers, volunteers extras.
- 50-79%: cooperative but efficient; short answers; occasionally asks how
  many more questions there are.
- 25-49%: impatient; 1-sentence answers; after ~10 questions starts pushing
  ("can we wrap this up?"), skips details unless pressed.
- below 25%: wants this over NOW; minimal answers; after ~6 questions
  demands it finish and threatens to leave; ignores non-essential questions."""

GENERATOR_PROMPT = """Invent ONE fake business-owner persona for testing a
chatbot-requirements interviewer. Make it MAXIMALLY DIFFERENT from these
already generated in this batch: [{previous}]. Vary industry (food, trades,
professional services, retail, beauty, automotive, events, manufacturing,
health...), company size (solo up to ~30 staff), personality, language style
(some mix Arabic or Spanish words in).

Build the persona's GROUND-TRUTH IDENTITY as a JSON object using EXACTLY
these field names (this is the interviewer's output schema):
{fields}

COMPLETENESS TARGET: this owner knows about {pct}% of their own business.
- Fill about {pct}% of the fields that APPLY to this business with concrete,
  internally consistent values (real prices with currency, real hours, named
  team members, a numeric budget...). Lists of objects may be simplified to
  lists of descriptive strings.
- Every remaining APPLICABLE field goes into unknown_fields: things this
  owner genuinely has not decided or does not know ("no idea what budget",
  "hours change weekly, never fixed them"). During the interview they will
  honestly say they don't know these.
- Fields that DO NOT APPLY to this business (e.g. venue_capacity for a
  remote consultant): omit them entirely, and do NOT count them in the
  percentage.

Output ONLY one JSON object:
{{"name": "<owner name>", "industry": "<industry>",
  "company_size": "<solo / 3 staff / 12 staff ...>",
  "traits": "<personality + communication style, one line>",
  "identity": {{...ground truth...}},
  "unknown_fields": ["<field name>", ...]}}"""

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


def _analyze(cases: list, prompt_text: str, schema_text: str) -> tuple[str, dict]:
    """The Opus analysis call. On this input size the model's invisible
    reasoning can eat a small max_tokens whole and leave an empty report
    (that is exactly what happened once), so: generous budget first, and a
    thinking-disabled retry as the fallback."""
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage
    from requirements_bot import _blocks_to_text
    content = (ANALYST_PROMPT.replace("{n}", str(len(cases)))
               + f"\n\n=== CURRENT INTERVIEWER PROMPT ===\n{prompt_text}\n"
               + f"\n=== CURRENT OUTPUT SCHEMA (models.py) ===\n{schema_text}\n\n"
               + "\n".join(cases))
    usage = {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}
    for llm in (ChatAnthropic(model=ANALYST_MODEL, max_tokens=30000),
                ChatAnthropic(model=ANALYST_MODEL, max_tokens=16000,
                              thinking={"type": "disabled"})):
        reply = llm.invoke([HumanMessage(content=content)])
        _acc(usage, _usage_of(reply))
        report = _blocks_to_text(reply.content).strip()
        if report:
            return report, usage
    raise RuntimeError("analyst returned an empty report twice")


def reanalyze(test_id: int) -> dict:
    """Redo ONLY the analysis stage of a finished test (its interviews are
    archived and paid for — never re-run them for a failed/empty report)."""
    t = store.sim_test(test_id)
    if not t or not t.get("run_ids"):
        return {"error": f"test {test_id} has no runs"}
    threading.Thread(target=_reanalyze_exec, args=(test_id,), daemon=True).start()
    return {"test_id": test_id}


def _reanalyze_exec(test_id: int) -> None:
    try:
        t = store.sim_test(test_id)
        store.sim_update_test(test_id, status="analyzing")
        root = Path(__file__).parent.parent
        prompt_text = (root / "interviewer_prompt.txt").read_text(encoding="utf-8")
        schema_text = (root / "models.py").read_text(encoding="utf-8")
        cases = []
        for rid in t["run_ids"]:
            r = store.sim_run(rid)
            convo = "\n".join(
                f"{'BOT' if x['who'] == 'bot' else 'OWNER'}: {x['text']}"
                for x in (r["transcript"] or []))
            brief = json.dumps(r["brief"], ensure_ascii=False) if r["brief"] else "(none)"
            p = store.sim_persona(r["persona_id"]) or {}
            cases.append(
                f"=== CASE {rid}: {r['persona_name']} — {p.get('industry')} — "
                f"{p.get('company_size')} — {p.get('traits')} ===\n"
                f"--- TRANSCRIPT (INPUT) ---\n{convo}\n"
                f"--- FINAL BRIEF (OUTPUT) ---\n{brief}\n")
        report, a_usage = _analyze(cases, prompt_text, schema_text)
        usage = t.get("usage") or {}
        usage["analyst"] = a_usage
        extra = _cost_of(ANALYST_MODEL, a_usage)
        store.sim_update_test(
            test_id, status="completed", report=report, usage=usage,
            cost_usd=round((t.get("cost_usd") or 0) + extra, 4),
            finished_ts=time.time())
    except Exception as e:
        store.sim_update_test(test_id, status="failed",
                              error=f"reanalysis: {type(e).__name__}: {e}",
                              finished_ts=time.time())


def _run_sweep(test_id: int, count: int) -> None:
    from concurrent.futures import ThreadPoolExecutor
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage
        from requirements_bot import _blocks_to_text

        # 1) generate the fake identities: full schema-shaped ground truth,
        # each persona at its own rung of the completeness ladder
        from models import BusinessRequirements
        field_names = ", ".join(BusinessRequirements.model_fields)
        store.sim_update_test(test_id, status="generating")
        gen = ChatAnthropic(model=GENERATOR_MODEL, max_tokens=8000)
        g_usage = {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}
        persona_ids, summaries = [], []
        for i in range(count):
            level = COMPLETENESS_LADDER[i % len(COMPLETENESS_LADDER)]
            patience = PATIENCE_LADDER[i % len(PATIENCE_LADDER)]
            prompt = (GENERATOR_PROMPT
                      .replace("{previous}", "; ".join(summaries) or "(none yet)")
                      .replace("{fields}", field_names)
                      .replace("{pct}", str(int(level * 100))))
            # a reply without valid JSON must not kill the whole sweep:
            # retry, telling the model what went wrong
            p, last_err, msgs = None, None, [HumanMessage(content=prompt)]
            for _ in range(3):
                reply = gen.invoke(msgs)
                _acc(g_usage, _usage_of(reply))
                text = _blocks_to_text(reply.content)
                try:
                    p = json.loads(text[text.index("{"):text.rindex("}") + 1])
                    break
                except (ValueError, json.JSONDecodeError) as e:
                    last_err = e
                    from langchain_core.messages import AIMessage
                    msgs = [HumanMessage(content=prompt),
                            AIMessage(content=text or "(empty)"),
                            HumanMessage(content=(
                                "FORMAT ERROR: that was not one valid JSON "
                                "object. Resend the same persona as ONE valid "
                                "JSON object only, no other text."))]
            if p is None:
                raise RuntimeError(
                    f"persona generation failed after 3 attempts: {last_err}")
            identity = p.get("identity") or {}
            unknown = p.get("unknown_fields") or []
            content = (
                "GROUND-TRUTH IDENTITY (JSON — everything this owner knows "
                "about their business):\n"
                + json.dumps(identity, ensure_ascii=False, indent=1)
                + "\n\nFIELDS THIS OWNER GENUINELY DOES NOT KNOW OR HAS NOT "
                "DECIDED (answer honestly that you don't know when asked):\n"
                + (", ".join(unknown) or "(none)")
                + "\n" + PATIENCE_RULE.replace("{pct}", str(int(patience * 100))))
            persona_ids.append(store.sim_add_persona(
                p.get("name", f"Persona {i+1}"), "ai", content,
                industry=p.get("industry"), company_size=p.get("company_size"),
                traits=p.get("traits"), identity=identity,
                completeness=level, patience=patience))
            summaries.append(f"{p.get('name')} ({p.get('industry')}, "
                             f"{p.get('company_size')})")

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
        report, a_usage = _analyze(cases, prompt_text, schema_text)

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


def _empty(v) -> bool:
    return v is None or v == [] or v == {} or (isinstance(v, str) and not v.strip())


def _score_run(persona: dict, brief: dict | None) -> tuple[float | None, list]:
    """Extraction score against the persona's ground-truth identity: of the
    fields the owner actually KNEW, how many did the interview capture?
    Returns (score 0..1, missed field names). None when there is no ground
    truth (scripted/legacy personas) or no brief."""
    identity = persona.get("identity")
    if isinstance(identity, str):
        try:
            identity = json.loads(identity)
        except Exception:
            identity = None
    if not identity or not brief:
        return None, []
    known = [k for k, v in identity.items() if not _empty(v) and k in brief]
    if not known:
        return None, []
    missed = [k for k in known if _empty(brief.get(k))]
    return round(1 - len(missed) / len(known), 3), missed


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
        brief = turn.requirements.model_dump() if turn else None
        score, missed = _score_run(persona, brief)
        store.sim_update_run(
            run_id, status="completed",
            interview_complete=int(bot.complete),
            transcript=transcript, brief=brief,
            score=score, missed=missed,
            usage={"bot": bot.usage, "persona": p_usage},
            cost_usd=round(cost, 4), finished_ts=time.time())
    except Exception as e:
        store.sim_update_run(
            run_id, status="failed", transcript=transcript,
            error=f"{type(e).__name__}: {e}", finished_ts=time.time())
