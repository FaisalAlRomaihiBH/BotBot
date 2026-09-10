# intake.py — EXPERIMENT: Birdie as a discovery interviewer for businesses that want a chatbot.
# Self-contained on purpose: delete this file and the original project is untouched.
#
# NEW: the business owner can drop real materials (chat exports .txt/.md/.csv,
# screenshots .png/.jpg) into the uploads/ folder and type /files during the
# interview. Birdie analyzes them and its questions become evidence-driven.
import base64
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

llm = ChatAnthropic(model="claude-sonnet-5")

UPLOADS_DIR = Path("uploads")
TEXT_EXTS = {".txt", ".md", ".csv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


# ---- The intake form: every field Optional because it starts empty and fills up ----
class BusinessRequirements(BaseModel):
    contact_name: Optional[str] = None           # the person we're interviewing
    business_name: Optional[str] = None
    industry: Optional[str] = None
    business_description: Optional[str] = None
    problem_to_solve: Optional[str] = None      # WHY they want a chatbot
    target_audience: Optional[str] = None        # who will talk to it
    channels: Optional[list[str]] = None         # website, WhatsApp, Instagram...
    integrations: Optional[list[str]] = None     # booking system, CRM, order DB...
    conversation_volume: Optional[str] = None    # rough conversations/day, hours coverage
    languages: Optional[list[str]] = None
    success_criteria: Optional[str] = None       # what "working" means to them
    budget: Optional[str] = None
    timeline: Optional[str] = None
    # --- the chatbot's actual knowledge, gathered during the interview ---
    services_and_pricing: Optional[list[str]] = None  # every service, price, discount, subscription
    faq_answers: Optional[list[str]] = None           # confirmed question->answer pairs the bot can use
    business_policies: Optional[list[str]] = None     # cancellations, rush requests, coverage area, VAT...
    escalation_rules: Optional[list[str]] = None      # what goes to a human, to whom, via what channel
    open_items: Optional[list[str]] = None            # things the owner was unsure about / left unresolved
    additional_notes: Optional[list[str]] = None      # important facts that fit no other field — never lose a fact


# ---- What the analysis of uploaded materials produces ----
class ConversationAnalysis(BaseModel):
    inquiry_categories: list[str]    # e.g. "Appointment booking (~40%): customers ask for slots"
    resolved_patterns: list[str]     # inquiries where the materials SHOW how staff answers
    knowledge_gaps: list[str]        # inquiries whose resolution is NOT visible -> ask the owner
    facts_learned: list[str]         # hard facts about the business found in the materials


# ---- What the interviewer model must output EVERY turn ----
class InterviewTurn(BaseModel):
    requirements: BusinessRequirements   # the form, updated with everything learned so far
    next_message: str                    # the question (or final summary) to show the user
    interview_complete: bool             # True only when enough is gathered and confirmed
    run_file_analysis: bool = False      # True when the owner just said their files are ready


interview_parser = PydanticOutputParser(pydantic_object=InterviewTurn)
analysis_parser = PydanticOutputParser(pydantic_object=ConversationAnalysis)

NO_MATERIALS = "No materials shared yet."

# The interviewer's system prompt lives in its own file: it is the EVOLVABLE part
# of BotBot — the self-improvement loop (evolve.py) may rewrite that file, never this code.
PROMPT_FILE = Path(__file__).parent / "interviewer_prompt.txt"

interview_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", PROMPT_FILE.read_text(encoding="utf-8")),
        ("placeholder", "{chat_history}"),
        ("human", "{query}"),
    ]
).partial(format_instructions=interview_parser.get_format_instructions())

# No tools needed for the interview -> no agent/executor; a plain chain is enough.
interview_chain = interview_prompt | llm


def _blocks_to_text(content) -> str:
    """Claude replies with a list of content blocks; keep only the text ones."""
    if isinstance(content, list):
        return "".join(block["text"] for block in content if block["type"] == "text")
    return content


def ask(question: str, chat_history: list, analysis_text: str = NO_MATERIALS):
    reply = interview_chain.invoke({
        "query": question,
        "chat_history": chat_history,
        "analysis": analysis_text,
    })
    output = _blocks_to_text(reply.content)
    return interview_parser.parse(output), output


# ---------------- File analysis pass ----------------

def load_upload_blocks() -> tuple[list, list[str]]:
    """Read everything in uploads/ into Claude content blocks (text + images).

    Returns (blocks, loaded_filenames)."""
    blocks, names = [], []
    if not UPLOADS_DIR.exists():
        return blocks, names
    for path in sorted(UPLOADS_DIR.iterdir()):
        ext = path.suffix.lower()
        if ext in TEXT_EXTS:
            blocks.append({
                "type": "text",
                "text": f"--- FILE: {path.name} ---\n{path.read_text(encoding='utf-8', errors='replace')}",
            })
            names.append(path.name)
        elif ext in IMAGE_EXTS:
            media = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            blocks.append({"type": "text", "text": f"--- SCREENSHOT: {path.name} ---"})
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media,
                    "data": base64.b64encode(path.read_bytes()).decode(),
                },
            })
            names.append(path.name)
    return blocks, names


ANALYSIS_INSTRUCTIONS = """
You are analyzing real customer-conversation materials (chat exports, screenshots)
shared by a business owner who wants a chatbot built.

Study them and produce:
- inquiry_categories: the kinds of inquiries customers actually send, with rough
  share and a one-line description each.
- resolved_patterns: inquiry types where the materials SHOW how staff answers
  (include the observed answer) — a chatbot could learn these directly.
- knowledge_gaps: inquiry types where the resolution is NOT visible (staff said
  "let me check", moved to a call, answered inconsistently, or the answer clearly
  depends on knowledge not in the materials). For each, describe the specific
  observed example — these become interview questions for the owner.
- facts_learned: hard facts about the business visible in the materials (hours,
  services, prices, channels, languages used...).

Wrap your entire output in this format and provide no other text
"""


def analyze_uploads() -> tuple[Optional[ConversationAnalysis], list[str]]:
    """Run the analysis pass over uploads/. Returns (analysis, filenames)."""
    blocks, names = load_upload_blocks()
    if not blocks:
        return None, []
    content = [{"type": "text", "text": ANALYSIS_INSTRUCTIONS + analysis_parser.get_format_instructions()}] + blocks
    reply = llm.invoke([HumanMessage(content=content)])
    return analysis_parser.parse(_blocks_to_text(reply.content)), names


def analysis_to_prompt_text(analysis: ConversationAnalysis) -> str:
    """Format the analysis for injection into the interviewer's system prompt."""
    def bullet(items):
        return "\n".join(f"  - {i}" for i in items) or "  (none)"
    return (
        f"Inquiry categories observed:\n{bullet(analysis.inquiry_categories)}\n"
        f"Resolved patterns (already learnable, do not ask):\n{bullet(analysis.resolved_patterns)}\n"
        f"KNOWLEDGE GAPS (ask about these, one at a time, citing the example):\n{bullet(analysis.knowledge_gaps)}\n"
        f"Facts learned (fill the form with these, do not ask):\n{bullet(analysis.facts_learned)}"
    )


# ---------------- Chat loop ----------------
if __name__ == "__main__":
    chat_history = []
    analysis_text = NO_MATERIALS
    analysis_obj = None  # kept so the raw analysis can be saved with the brief

    UPLOADS_DIR.mkdir(exist_ok=True)  # so the folder exists when Birdie mentions it
    greeting = ("Hi! I help businesses figure out exactly what they need from a "
                "chatbot. Before we start — what's your name?")
    print("Birdie:", greeting)
    # Put the greeting in history so the model knows it already asked for the name
    chat_history.append(("ai", greeting))

    while True:
        try:
            user_msg = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_msg:
            continue
        if user_msg.lower() in ("quit", "exit"):
            break

        turn, raw_text = ask(user_msg, chat_history, analysis_text)
        print("\nBirdie:", turn.next_message)

        chat_history.append(("human", user_msg))
        chat_history.append(("ai", raw_text))

        # --- Birdie decided the owner's files are ready: analyze, then react ---
        if turn.run_file_analysis:
            print("\n[Analyzing files in uploads/ ...]")
            analysis, names = analyze_uploads()
            if analysis is None:
                turn, raw_text = ask(
                    "(System note: the uploads folder is empty — no readable files. "
                    "Gently tell the owner nothing was found and how to add files, "
                    "then continue the interview.)",
                    chat_history, analysis_text,
                )
            else:
                analysis_obj = analysis
                analysis_text = analysis_to_prompt_text(analysis)
                print(f"[Analyzed {len(names)} file(s): {', '.join(names)}]")
                turn, raw_text = ask(
                    "(System note: the analysis of the shared materials is now in your "
                    "instructions. React to it: mention 1-2 useful things you learned, "
                    "then ask about the first knowledge gap.)",
                    chat_history, analysis_text,
                )
            print("\nBirdie:", turn.next_message)
            chat_history.append(("human", "(files were analyzed)"))
            chat_history.append(("ai", raw_text))

        if turn.interview_complete:
            # The deliverable: the brief PLUS the raw materials analysis (booking
            # flows, resolved patterns etc. are gold for whoever builds the bot)
            import json
            brief = {
                "requirements": turn.requirements.model_dump(),
                "conversation_analysis": analysis_obj.model_dump() if analysis_obj else None,
            }
            with open("requirements_brief.json", "w", encoding="utf-8") as f:
                json.dump(brief, f, indent=2, ensure_ascii=False)
            print("\n[Saved the full brief to requirements_brief.json]")
            break

    print("\nGoodbye!")
