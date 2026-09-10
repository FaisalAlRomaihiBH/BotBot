# tests/conftest.py — every test runs in a sandbox: temp files, fake LLM, no-op
# git. The real repo, prompt file and any live evolve run are never touched.
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import intake
import evolve
import evolve_until
from tests import fakes


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    runs = tmp_path / "evolve_runs"
    runs.mkdir()
    prompt = tmp_path / "interviewer_prompt.txt"
    prompt.write_text(
        "You are Birdie. Interview the owner warmly. " * 20
        + "\n{analysis}\n{format_instructions}\n", encoding="utf-8")

    monkeypatch.setattr(evolve, "ROOT", tmp_path)
    monkeypatch.setattr(evolve, "RUNS_DIR", runs)
    monkeypatch.setattr(evolve, "HISTORY_FILE", runs / "history.json")
    monkeypatch.setattr(evolve, "PROMPT_FILE", prompt)
    monkeypatch.setattr(evolve_until, "REPORTS_DIR", runs / "reports")
    monkeypatch.setattr(evolve_until, "POINTER_FILE", runs / "improve_pointer.json")

    git_calls = []
    monkeypatch.setattr(evolve, "git", lambda *a: git_calls.append(a) or "")

    llm = fakes.FakeLLM()
    monkeypatch.setattr(evolve, "llm", llm)
    monkeypatch.setattr(evolve, "strong_llm", llm)
    monkeypatch.setattr(intake, "ask", fakes.FakeAsk())
    monkeypatch.setattr(intake, "analyze_uploads",
                        lambda: (fakes.FAKE_ANALYSIS, ["whatsapp_export.txt"]))

    return {"root": tmp_path, "runs": runs, "prompt": prompt,
            "llm": llm, "git_calls": git_calls}
