# tests/fakes.py — offline stand-ins for every LLM call the evolve system makes.
# The FakeLLM routes on marker phrases in the prompt text, so the REAL prompts,
# parsers and guardrails in evolve.py run unchanged — only the model is fake.
import json
from types import SimpleNamespace

import intake


def persona_json(industry="Test Bakery Industry", personality="curt one-word answers"):
    return json.dumps({
        "owner_name": "Test Owner",
        "business_name": "Testly's",
        "industry": industry,
        "personality": personality,
        "background_facts": "Croissant $3. Open 7-15. Cash only. Unsure about delivery.",
        "whatsapp_export": "[10/09, 08:01] Customer: price of croissant?\n"
                           "[10/09, 08:02] Testly's: $3\n"
                           "[10/09, 08:05] Customer: do you deliver?\n"
                           "[10/09, 08:30] Testly's: let me check",
    })


def evaluation_json(top="Ask about delivery explicitly."):
    return json.dumps({
        "fact_capture": 7, "no_repeats": 8, "evidence_use": 6,
        "naturalness": 9, "completeness": 7, "efficiency": 8,
        "findings": [
            {"problem": "Never asked about payment methods",
             "excerpt": "Test Owner: cash only\nBirdie: Great! Next, your hours?"},
            {"problem": "Delivery dead-end from the export was not raised",
             "excerpt": "Customer: do you deliver?\nTestly's: let me check"},
        ],
        "top_improvement": top,
        "schema_suggestions": ["Add a payment_methods field"],
    })


GOOD_NEW_PROMPT = (
    "You are Birdie, an interviewer. " + "Ask sharp questions. " * 30 +
    "\nMaterials analysis:\n{analysis}\n\nAlways cover payment methods "
    "and every dead-end found in the materials.\n{format_instructions}\n"
)


class FakeLLM:
    """Routes each llm.invoke(prompt) to a canned reply by marker phrase."""

    def __init__(self, improve_reply=GOOD_NEW_PROMPT):
        self.improve_reply = improve_reply
        self.calls = []  # (kind, prompt) for assertions

    def invoke(self, text):
        if "creating a fictional business owner" in text:
            kind, out = "persona", persona_json()
        elif "role-playing" in text:
            kind, out = "owner_turn", "cash only. next question"
        elif "strict QA judge" in text:
            kind, out = "evaluate", evaluation_json()
        elif "You maintain the system prompt" in text:
            kind, out = "improve", self.improve_reply
        elif "plain-language hourly digest" in text:
            kind, out = "summary", json.dumps({
                "interviews": [{"cycle": 1, "issue": "Missed payment methods."},
                               {"cycle": 2, "issue": "Missed payment methods."}],
                "fix": "Birdie now always asks how customers pay.",
                "attention": ["Decide whether to add a payment field."]})
        else:
            raise AssertionError("FakeLLM got an unrecognized prompt:\n" + text[:200])
        self.calls.append((kind, text))
        return SimpleNamespace(content=out)


def make_turn(msg="And your hours?", complete=False, analysis=False):
    return intake.InterviewTurn(
        requirements=intake.BusinessRequirements(
            business_name="Testly's",
            services_and_pricing=["Croissant $3"]),
        next_message=msg, interview_complete=complete, run_file_analysis=analysis)


class FakeAsk:
    """Scripted replacement for intake.ask: analysis on turn 1, done on turn 3."""

    def __init__(self):
        self.n = 0

    def __call__(self, question, chat_history, analysis_text=intake.NO_MATERIALS):
        self.n += 1
        if self.n == 1:
            turn = make_turn("Files ready? Analyzing.", analysis=True)
        elif self.n < 4:
            turn = make_turn("Tell me about pricing.")
        else:
            turn = make_turn("Summary confirmed — we're done!", complete=True)
        return turn, "(raw model output)"


FAKE_ANALYSIS = intake.ConversationAnalysis(
    inquiry_categories=["Pricing (~50%)"],
    resolved_patterns=["Croissant price answered: $3"],
    knowledge_gaps=["Delivery question never answered"],
    facts_learned=["Cash only"])
