# tests/test_evolve.py — offline tests for the whole evolve system.
# Run:  python -m pytest tests -v
import json
from types import SimpleNamespace

import pytest

import evolve
import evolve_until
from tests import fakes


# ---------------- persona ----------------
def test_generate_persona_parses(sandbox):
    p = evolve.generate_persona(["old industry / old personality"])
    assert p.business_name == "Testly's"
    assert "croissant" in p.whatsapp_export.lower()
    # recently-used personas were shown to the generator for variety
    assert "old industry" in sandbox["llm"].calls[0][1]


# ---------------- interview ----------------
def test_run_interview_completes_and_analyzes(sandbox):
    p = evolve.Persona.model_validate_json(fakes.persona_json())
    result = evolve.run_interview(p)
    assert result["completed"] is True
    assert result["brief"]["business_name"] == "Testly's"
    assert result["analysis"]["knowledge_gaps"] == ["Delivery question never answered"]
    # the persona's fake export was placed as the only upload
    uploads = sandbox["root"] / "uploads"
    assert [f.name for f in uploads.iterdir()] == ["whatsapp_export.txt"]
    assert result["turns"] <= 2 * evolve.MAX_TURNS + 2


def test_run_interview_respects_max_turns(sandbox, monkeypatch):
    import intake
    never_done = lambda q, h, a=None: (fakes.make_turn("more?"), "raw")
    monkeypatch.setattr(intake, "ask", never_done)
    p = evolve.Persona.model_validate_json(fakes.persona_json())
    result = evolve.run_interview(p)
    assert result["completed"] is False  # hard stop, no infinite loop


# ---------------- evaluate ----------------
def test_evaluate_scores_and_excerpts(sandbox):
    p = evolve.Persona.model_validate_json(fakes.persona_json())
    ev = evolve.evaluate(p, {"transcript": [("Birdie", "hi")], "brief": {},
                             "completed": True, "turns": 5})
    assert evolve.avg_score(ev) == 7.5
    assert ev.findings[0].excerpt.startswith("Test Owner: cash only")
    # judge saw the fact sheet and the transcript
    prompt_sent = sandbox["llm"].calls[-1][1]
    assert "Croissant $3" in prompt_sent and "Birdie: hi" in prompt_sent


def test_fmt_finding_all_shapes():
    assert evolve.fmt_finding("plain old string") == "- plain old string"
    new = evolve.fmt_finding({"problem": "P", "excerpt": "a: x\nb: y"})
    assert new == "- P\n    | a: x\n    | b: y"
    obj = evolve.Finding(problem="P", excerpt="line")
    assert evolve.fmt_finding(obj) == "- P\n    | line"


# ---------------- improve guardrails ----------------
def test_improve_accepts_valid_prompt(sandbox):
    desc = evolve.improve_prompt("- finding", "- top")
    assert desc and "prompt updated" in desc
    assert "payment methods" in sandbox["prompt"].read_text(encoding="utf-8")


def test_improve_rejects_missing_placeholder(sandbox):
    sandbox["llm"].improve_reply = "New prompt without placeholders. " * 30
    before = sandbox["prompt"].read_text(encoding="utf-8")
    assert evolve.improve_prompt("- f", "- t") is None
    assert sandbox["prompt"].read_text(encoding="utf-8") == before


def test_improve_rejects_gutting(sandbox):
    before = sandbox["prompt"].read_text(encoding="utf-8")
    sandbox["llm"].improve_reply = "tiny {analysis} {format_instructions}"
    assert evolve.improve_prompt("- f", "- t") is None
    assert sandbox["prompt"].read_text(encoding="utf-8") == before


def test_improve_rejects_no_change(sandbox):
    sandbox["llm"].improve_reply = sandbox["prompt"].read_text(encoding="utf-8").strip()
    assert evolve.improve_prompt("- f", "- t") is None


# ---------------- full cycle ----------------
def test_run_cycle_register_only(sandbox):
    history = []
    entry = evolve.run_cycle(1, history, improve=False)
    assert entry["completed"] and entry["improved"] is False
    assert entry["findings"][0]["problem"] == "Never asked about payment methods"
    assert entry["findings"][0]["excerpt"]
    # register-only mode must never call the improver
    assert all(kind != "improve" for kind, _ in sandbox["llm"].calls)
    # history persisted, run artifacts written, cycle committed
    saved = json.loads((sandbox["runs"] / "history.json").read_text(encoding="utf-8"))
    assert len(saved) == 1
    run_dir = next(d for d in sandbox["runs"].iterdir() if d.name.startswith("cycle_"))
    assert {"persona.json", "interview.json", "evaluation.json"} <= {
        f.name for f in run_dir.iterdir()}
    assert ("add", "-A") in sandbox["git_calls"]


def test_run_cycle_with_improve(sandbox):
    entry = evolve.run_cycle(1, [], improve=True)
    assert entry["improved"] is True
    assert "payment methods" in sandbox["prompt"].read_text(encoding="utf-8")


def test_interviewer_prompt_hot_reloads(tmp_path, monkeypatch):
    """An improved prompt must reach Birdie on the NEXT turn, without a restart."""
    import intake
    f = tmp_path / "prompt.txt"
    monkeypatch.setattr(intake, "PROMPT_FILE", f)
    f.write_text("OLD RULES {analysis} {format_instructions}", encoding="utf-8")
    chain1 = intake._build_interview_chain()
    f.write_text("NEW RULES {analysis} {format_instructions}", encoding="utf-8")
    chain2 = intake._build_interview_chain()
    sys1 = chain1.first.messages[0].prompt.template
    sys2 = chain2.first.messages[0].prompt.template
    assert "OLD RULES" in sys1 and "NEW RULES" in sys2


# ---------------- hourly improve pass + PDF ----------------
def test_improve_pass_batches_and_writes_pdf(sandbox):
    history = []
    evolve.run_cycle(1, history, improve=False)
    evolve.run_cycle(2, history, improve=False)
    pdf = evolve_until.improve_pass(history)
    assert pdf and pdf.endswith(".pdf")
    assert (sandbox["runs"] / "reports").exists()
    # the improver saw findings from BOTH interviews, with excerpts
    improve_call = next(t for k, t in sandbox["llm"].calls if k == "improve")
    assert improve_call.count("Never asked about payment methods") == 2
    assert "    | Test Owner: cash only" in improve_call
    # entries flagged done (persisted) -> second pass has nothing to do
    assert all(h["improve_done"] for h in history)
    assert all(h["improve_done"] for h in json.loads(
        (sandbox["runs"] / "history.json").read_text()))
    assert evolve_until.improve_pass(history) is None


def test_improve_pass_pdf_handles_legacy_string_findings(sandbox):
    history = [{"cycle": 1, "persona": "old / legacy", "score": 6.0,
                "completed": True, "findings": ["a plain string finding"],
                "top_improvement": "do better", "schema_suggestions": []}]
    pdf = evolve_until.improve_pass(history)
    assert pdf and (sandbox["runs"] / "reports").exists()


def test_improve_pass_survives_guardrail_rejection(sandbox):
    sandbox["llm"].improve_reply = "broken, no placeholders " * 40
    history = []
    evolve.run_cycle(1, history, improve=False)
    pdf = evolve_until.improve_pass(history)  # still writes the report
    assert pdf is not None


def test_merge_histories_unions_renumbers_and_keeps_done_flags():
    ours = [{"cycle": 1, "stamp": "20260910_0700", "persona": "a", "score": 5.0},
            {"cycle": 2, "stamp": "20260910_0900", "persona": "c", "score": 7.0}]
    theirs = [{"cycle": 1, "stamp": "20260910_0700", "persona": "a", "score": 5.0,
               "improve_done": True},
              {"cycle": 2, "stamp": "20260910_0800", "persona": "b", "score": 6.0}]
    merged = evolve.merge_histories(ours, theirs)
    assert [e["stamp"] for e in merged] == ["20260910_0700", "20260910_0800", "20260910_0900"]
    assert [e["cycle"] for e in merged] == [1, 2, 3]
    assert merged[0]["improve_done"] is True  # a done flag from either side sticks


def test_improve_pass_migrates_legacy_pointer(sandbox):
    history = []
    evolve.run_cycle(1, history, improve=False)
    evolve.run_cycle(2, history, improve=False)
    pointer = sandbox["runs"] / "improve_pointer.json"
    pointer.write_text(json.dumps({"done": 1}))
    evolve_until.improve_pass(history)
    assert not pointer.exists()
    # entry 1 was skipped as already-done; entry 2 got improved
    improve_call = next(t for k, t in sandbox["llm"].calls if k == "improve")
    assert improve_call.count("Never asked about payment methods") == 1


def test_invoke_parsed_retries_malformed_output(sandbox):
    llm = sandbox["llm"]
    good = fakes.persona_json()

    class Flaky:
        n = 0
        def invoke(self, text):
            Flaky.n += 1
            content = "not json {{{" if Flaky.n == 1 else good
            return SimpleNamespace(content=content)

    p = evolve.invoke_parsed(Flaky(), "whatever", evolve.persona_parser)
    assert p.owner_name == "Test Owner" and Flaky.n == 2


def test_crashed_cycle_writes_error_log(sandbox, monkeypatch):
    monkeypatch.setattr(evolve, "run_interview",
                        lambda persona: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        evolve.run_cycle(1, [], improve=False)
    run_dir = next(d for d in sandbox["runs"].iterdir() if d.name.startswith("cycle_"))
    assert "RuntimeError: boom" in (run_dir / "error.log").read_text()
