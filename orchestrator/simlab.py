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


# Cheapest model everywhere in the LAB'S OWN machinery for now (user call,
# 2026-09-13). The RequirementsBot under test keeps its production model —
# testing a cheaper stand-in would find problems real clients never see.
GENERATOR_MODEL = "claude-haiku-4-5"
ANALYST_MODEL = "claude-haiku-4-5"

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

# Six more behavioral ladders, each on four rungs (1.0 best-case .. 0.15
# hardest-case). Balanced least-tested-first across the whole archive, like
# knowledge and patience.
EXTRA_RUNGS = [1.0, 0.7, 0.4, 0.15]
EXTRA_LADDERS = {
    "consistency": ("100%: never contradicts yourself. 70%: one slip you fix "
                    "if challenged. 40%: change one price or the hours "
                    "mid-interview without noticing. 15%: repeatedly "
                    "contradict earlier answers"),
    "clarity": ("100%: precise concrete answers. 70%: mostly clear, a vague "
                "word now and then. 40%: vague by default ('around noonish, "
                "depends') until pushed. 15%: rambling stories with the "
                "actual answer buried"),
    "trust": ("100%: open with every detail. 70%: slightly guarded. 40%: "
              "won't give budget or address until the bot explains why. "
              "15%: suspicious, questions why everything is needed"),
    "language_mix": ("100%: pure English. 70%: a few foreign words dropped "
                     "in. 40%: heavy mixing of Arabic or Spanish mid-"
                     "sentence. 15%: mostly Arabic or Spanish, little "
                     "English"),
    "typing": ("100%: clean text. 70%: casual, some typos. 40%: no "
               "punctuation, abbreviations. 15%: voice-note-style fragments "
               "('ya so bsically 25bd fr the big 1')"),
    "focus": ("100%: stays on topic. 70%: brief tangents. 40%: drifts into "
              "stories, needs redirecting. 15%: keeps asking the "
              "interviewer questions back instead of answering"),
}

# Categorical dimensions, assigned (not left to chance) so coverage balances.
CATEGORY_OPTIONS = {
    "business_model": ["walk-in", "appointment-based", "B2B accounts",
                       "online orders", "hybrid"],
    "data_situation": ["all on paper", "phone notes", "spreadsheets",
                       "real systems"],
    "requested_scope": ["FAQ only", "lead capture", "booking", "ordering"],
    "decision_structure": ["sole decider", "spouse must agree",
                           "skeptical partner in background"],
}

# One-off special behaviors ('none' weighted 3x so most interviews stay
# ordinary). drops_out is enforced by the engine via the exit token.
BEHAVIOR_WEIGHT = {"none": 3, "drops_out": 1, "asks_meta": 1,
                   "corrects_later": 1}
DROP_TOKEN = "[LEFT_CONVERSATION]"
BEHAVIOR_RULES = {
    "none": "",
    "drops_out": (f"SPECIAL BEHAVIOR: at a natural point roughly two thirds "
                  f"of the way through the interview, you silently stop "
                  f"responding — output exactly {DROP_TOKEN} and nothing "
                  f"else. Never announce you are leaving first."),
    "asks_meta": ("SPECIAL BEHAVIOR: two or three times during the "
                  "interview, ask about the process itself (what will this "
                  "bot cost me? is my data private? how long until it's "
                  "ready?) before answering the question you were asked."),
    "corrects_later": ("SPECIAL BEHAVIOR: early on, state one important "
                       "fact slightly WRONG (a price or the closing hour). "
                       "Several exchanges later, correct yourself "
                       "unprompted ('wait, I said X before, it's actually "
                       "Y')."),
}

GENERATOR_PROMPT = """Invent ONE fake business-owner persona for testing a
chatbot-requirements interviewer. Make it MAXIMALLY DIFFERENT from these
already generated in this batch: [{previous}]. Vary industry (food, trades,
professional services, retail, beauty, automotive, events, manufacturing,
health...), company size (solo up to ~30 staff), personality, language style
(some mix Arabic or Spanish words in).

GLOBAL COVERAGE SO FAR (every persona ever tested, across all tests):
{coverage}
ASSIGNED ATTRIBUTES — the persona MUST match these exactly, and the
identity must be consistent with them:
{assigned}
BALANCE RULE: pick an industry, company size, language mix, and requested
channels that are UNDER-represented or absent above, so total coverage
across all tests stays balanced. Never repeat the most-tested industry.

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


def _bucket(v: float | None, edges: list) -> str | None:
    if v is None:
        return None
    for hi, label in edges:
        if v >= hi:
            return label
    return edges[-1][1]


def _coverage_summary() -> tuple[str, dict]:
    """What the whole archive has already tested, so a new sweep can balance
    against it. Returns (text for the generator prompt, per-dimension usage
    counts for least-tested-first assignment)."""
    personas = store.sim_personas()
    ind, size, langs, chans = {}, {}, {}, {}
    know_counts = {r: 0 for r in COMPLETENESS_LADDER}
    pat_counts = {r: 0 for r in PATIENCE_LADDER}
    ladder_counts = {n: {r: 0 for r in EXTRA_RUNGS} for n in EXTRA_LADDERS}
    cat_counts = {n: {o: 0 for o in opts}
                  for n, opts in CATEGORY_OPTIONS.items()}
    beh_counts = {b: 0 for b in BEHAVIOR_WEIGHT}
    for p in personas:
        try:
            feats = json.loads(p.get("features") or "{}")
        except Exception:
            feats = {}
        for n, r in (feats.get("ladders") or {}).items():
            if n in ladder_counts and r in ladder_counts[n]:
                ladder_counts[n][r] += 1
        for n in CATEGORY_OPTIONS:
            v = feats.get(n)
            if v in cat_counts[n]:
                cat_counts[n][v] += 1
        b = feats.get("behavior")
        if b in beh_counts:
            beh_counts[b] += 1
        if p.get("industry"):
            ind[p["industry"]] = ind.get(p["industry"], 0) + 1
        s = p.get("company_size")
        if s:
            size[s] = size.get(s, 0) + 1
        if p.get("completeness") in know_counts:
            know_counts[p["completeness"]] += 1
        if p.get("patience") in pat_counts:
            pat_counts[p["patience"]] += 1
        try:
            identity = json.loads(p.get("identity") or "{}")
        except Exception:
            identity = {}
        for key, m in (("languages", langs), ("channels", chans)):
            v = identity.get(key)
            for item in (v if isinstance(v, list) else [v] if v else []):
                item = str(item)[:24]
                m[item] = m.get(item, 0) + 1

    def fmt(name, m):
        if not m:
            return f"{name}: (nothing tested yet)"
        return name + ": " + ", ".join(
            f"{k} x{v}" for k, v in sorted(m.items(), key=lambda e: -e[1])[:15])
    text = "\n".join([fmt("Industries", ind), fmt("Company sizes", size),
                      fmt("Languages", langs), fmt("Channels", chans)])
    counts = {"know": know_counts, "pat": pat_counts,
              "ladders": ladder_counts, "cats": cat_counts,
              "beh": beh_counts}
    return text, counts


def start_test(kind: str, params: dict) -> dict:
    if kind in ("persona_sweep", "stress"):
        count = max(2, min(8, int(params.get("count") or 4)))
        pin = None
        if kind == "stress":
            dim = str(params.get("dimension") or "patience")
            try:
                val = float(params.get("level") or 0.15)
            except (TypeError, ValueError):
                val = 0.15
            pin = (dim, val)
        test_id = store.sim_create_test(kind, {"count": count} | (
            {"dimension": pin[0], "level": pin[1]} if pin else {}))
        threading.Thread(target=_run_sweep, args=(test_id, count, pin),
                         daemon=True).start()
        return {"test_id": test_id}
    if kind == "regression_pin":
        base_id = int(params.get("base_test_id") or 0)
        base = store.sim_test(base_id)
        if not base or not base.get("run_ids"):
            return {"error": f"base test {base_id} has no runs"}
        test_id = store.sim_create_test(kind, {
            "count": len(base["run_ids"]), "base_test_id": base_id})
        threading.Thread(target=_run_regression, args=(test_id, base_id),
                         daemon=True).start()
        return {"test_id": test_id}
    return {"error": f"unknown test kind {kind}"}


def _run_regression(test_id: int, base_id: int) -> None:
    """Re-run the EXACT personas of a previous test against the current bot
    and diff the extraction scores — the true before/after instrument."""
    from concurrent.futures import ThreadPoolExecutor
    try:
        base = store.sim_test(base_id)
        base_runs = [store.sim_run(r) for r in base["run_ids"]]
        persona_ids = [r["persona_id"] for r in base_runs if r]
        from requirements_bot import RequirementsBot
        bot_model = RequirementsBot.__init__.__defaults__[0]
        store.sim_update_test(test_id, status="running", params={
            "count": len(persona_ids), "base_test_id": base_id,
            "models": {"bot": bot_model, "persona": PERSONA_MODEL}})
        run_ids = [store.sim_create_run(pid) for pid in persona_ids]
        store.sim_update_test(test_id, run_ids=run_ids)
        with ThreadPoolExecutor(max_workers=len(run_ids)) as ex:
            list(ex.map(lambda rp: _execute(rp[0], store.sim_persona(rp[1])),
                        zip(run_ids, persona_ids)))
        store.sim_update_test(test_id, status="analyzing")
        lines = [f"# Regression vs Test {base_id}", "",
                 "| Persona | Old score | New score | Delta | "
                 "Newly captured | Newly missed |",
                 "|---|---|---|---|---|---|"]
        for old, rid in zip(base_runs, run_ids):
            new = store.sim_run(rid)
            om = set(old.get("missed") or [])
            nm = set(new.get("missed") or [])
            osc, nsc = old.get("score"), new.get("score")
            delta = (f"{(nsc-osc)*100:+.0f}%"
                     if osc is not None and nsc is not None else "—")
            fmt_s = lambda s: f"{s*100:.0f}%" if s is not None else "—"
            lines.append(
                f"| {new['persona_name']} | {fmt_s(osc)} | {fmt_s(nsc)} | "
                f"{delta} | {', '.join(sorted(om - nm)) or '—'} | "
                f"{', '.join(sorted(nm - om)) or '—'} |")
        runs_cost = sum((store.sim_run(r) or {}).get("cost_usd") or 0
                        for r in run_ids)
        store.sim_update_test(
            test_id, status="completed", report="\n".join(lines),
            cost_usd=round(runs_cost, 4), finished_ts=time.time())
    except Exception as e:
        store.sim_update_test(test_id, status="failed",
                              error=f"{type(e).__name__}: {e}",
                              finished_ts=time.time())


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
    if "haiku" in ANALYST_MODEL:   # no adaptive thinking to eat the budget
        attempts = (ChatAnthropic(model=ANALYST_MODEL, max_tokens=8000),
                    ChatAnthropic(model=ANALYST_MODEL, max_tokens=8000))
    else:
        attempts = (ChatAnthropic(model=ANALYST_MODEL, max_tokens=30000),
                    ChatAnthropic(model=ANALYST_MODEL, max_tokens=16000,
                                  thinking={"type": "disabled"}))
    for llm in attempts:
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
        usage["analyst"] = a_usage | {"model": ANALYST_MODEL}
        extra = _cost_of(ANALYST_MODEL, a_usage)
        store.sim_update_test(
            test_id, status="completed", report=report, usage=usage,
            cost_usd=round((t.get("cost_usd") or 0) + extra, 4),
            finished_ts=time.time())
    except Exception as e:
        store.sim_update_test(test_id, status="failed",
                              error=f"reanalysis: {type(e).__name__}: {e}",
                              finished_ts=time.time())


def _run_sweep(test_id: int, count: int, pin: tuple | None = None) -> None:
    from concurrent.futures import ThreadPoolExecutor
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage
        from requirements_bot import _blocks_to_text

        # Register the MODEL LINE-UP of this test permanently: which model
        # ran each stage. That makes model choice a comparable dimension of
        # the archive — the same feature combination can later be re-tried
        # on a different model and compared test-to-test.
        from requirements_bot import RequirementsBot
        bot_model = RequirementsBot.__init__.__defaults__[0]
        store.sim_update_test(test_id, params={
            "count": count,
            "models": {"generator": GENERATOR_MODEL, "bot": bot_model,
                       "persona": PERSONA_MODEL, "analyst": ANALYST_MODEL}})

        # 1) generate the fake identities: full schema-shaped ground truth,
        # each persona at its own rung of the completeness ladder
        from models import BusinessRequirements
        field_names = ", ".join(BusinessRequirements.model_fields)
        store.sim_update_test(test_id, status="generating")
        gen = ChatAnthropic(model=GENERATOR_MODEL, max_tokens=8000)
        g_usage = {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}
        # BALANCING: the archive's cumulative coverage steers this sweep —
        # the generator is told what is over/under-tested, and the ladder
        # rungs are handed out least-tested-first so knowledge/patience
        # levels stay even across ALL tests, not just within one.
        coverage_text, cov = _coverage_summary()
        persona_ids, summaries = [], []
        for i in range(count):
            level = min(cov["know"], key=lambda r: (cov["know"][r], -r))
            patience = min(cov["pat"], key=lambda r: (cov["pat"][r], -r))
            cov["know"][level] += 1
            cov["pat"][patience] += 1
            ladders = {}
            for n in EXTRA_LADDERS:
                r = min(cov["ladders"][n],
                        key=lambda x: (cov["ladders"][n][x], -x))
                cov["ladders"][n][r] += 1
                ladders[n] = r
            cats = {}
            for n in CATEGORY_OPTIONS:
                o = min(cov["cats"][n], key=lambda x: cov["cats"][n][x])
                cov["cats"][n][o] += 1
                cats[n] = o
            behavior = min(cov["beh"],
                           key=lambda b: cov["beh"][b] / BEHAVIOR_WEIGHT[b])
            cov["beh"][behavior] += 1
            # honor a stress-test pin: one dimension fixed for every persona
            if pin:
                dim, val = pin
                if dim == "knowledge":
                    level = val
                elif dim == "patience":
                    patience = val
                elif dim in ladders:
                    ladders[dim] = val
            features = {"ladders": ladders, **cats, "behavior": behavior}
            assigned = "\n".join(
                [f"- {n}: {o}" for n, o in cats.items()]
                + [f"- {n} level {int(r*100)}% ({EXTRA_LADDERS[n]})"
                   for n, r in ladders.items()]
                + ([f"- special behavior: {behavior}"]
                   if behavior != "none" else []))
            prompt = (GENERATOR_PROMPT
                      .replace("{previous}", "; ".join(summaries) or "(none yet)")
                      .replace("{coverage}", coverage_text)
                      .replace("{assigned}", assigned)
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
                + "\n" + PATIENCE_RULE.replace("{pct}", str(int(patience * 100)))
                + "\n\nBEHAVIORAL LADDERS (follow each at its level):\n"
                + "\n".join(f"- {n} {int(r*100)}%: {EXTRA_LADDERS[n]}"
                             for n, r in ladders.items())
                + ("\n\n" + BEHAVIOR_RULES[behavior]
                   if BEHAVIOR_RULES[behavior] else ""))
            persona_ids.append(store.sim_add_persona(
                p.get("name", f"Persona {i+1}"), "ai", content,
                industry=p.get("industry"), company_size=p.get("company_size"),
                traits=p.get("traits"), identity=identity,
                completeness=level, patience=patience, features=features))
            summaries.append(f"{p.get('name')} ({p.get('industry')}, "
                             f"{p.get('company_size')})")
        # partial usage right away, so the UI can price each stage live
        store.sim_update_test(test_id, usage={
            "generator": g_usage | {"model": GENERATOR_MODEL}})

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
            usage={"generator": g_usage | {"model": GENERATOR_MODEL},
                   "analyst": a_usage | {"model": ANALYST_MODEL}},
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
            if DROP_TOKEN in owner_msg:
                # the persona silently left — archive the fact and stop
                transcript.append({"who": "persona",
                                   "text": "(left the conversation)"})
                store.sim_update_run(run_id, transcript=transcript)
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
            usage={"bot": bot.usage | {"model": bot.model},
                   "persona": p_usage | {"model": PERSONA_MODEL}},
            cost_usd=round(cost, 4), finished_ts=time.time())
    except Exception as e:
        store.sim_update_run(
            run_id, status="failed", transcript=transcript,
            error=f"{type(e).__name__}: {e}", finished_ts=time.time())
