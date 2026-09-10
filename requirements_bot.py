# requirements_bot.py — RequirementsBot: a discovery interviewer for businesses
# that want a chatbot built.
#
# The bot conducts a natural conversation with a business owner, fills in a
# BusinessRequirements form as it learns facts, and can analyze real customer
# materials (chat exports .txt/.md/.csv, screenshots .png/.jpg) the owner drops
# into the uploads/ folder — its questions then become evidence-driven.
#
# All conversation state lives inside a RequirementsBot instance, and
# to_dict()/from_dict() make that state serializable, so an interview can span
# processes (see persona_test.py) or run in one sitting (see main.py).
import base64
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser

from models import BusinessRequirements, ConversationAnalysis, InterviewTurn

load_dotenv()

ROOT = Path(__file__).parent


class MaterialsAnalyzer:
    """Reads the owner's uploaded materials and distills them into a
    ConversationAnalysis the interviewer can work from."""

    TEXT_EXTS = {".txt", ".md", ".csv"}
    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    INSTRUCTIONS = """
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
- notable_incidents: specific single events worth asking the owner about, each
  written as "<date/time from the material> — <what happened> (quote: "...")".
  Include long waits before a reply, complaints, apologies, lost or abandoned
  customers, unanswered messages, and promises made. Use the timestamps exactly
  as they appear in the material; if a message has no timestamp, say
  "(no timestamp)" rather than inventing one. These get raised verbatim in the
  live interview, so they must be quotable.

Wrap your entire output in this format and provide no other text
"""

    def __init__(self, llm, uploads_dir: Path):
        self.llm = llm
        self.uploads_dir = uploads_dir
        self.parser = PydanticOutputParser(pydantic_object=ConversationAnalysis)

    def load_blocks(self) -> tuple[list, list[str]]:
        """Read everything in uploads/ into Claude content blocks (text + images).

        Returns (blocks, loaded_filenames)."""
        blocks, names = [], []
        if not self.uploads_dir.exists():
            return blocks, names
        for path in sorted(self.uploads_dir.iterdir()):
            ext = path.suffix.lower()
            if ext in self.TEXT_EXTS:
                blocks.append({
                    "type": "text",
                    "text": f"--- FILE: {path.name} ---\n"
                            f"{path.read_text(encoding='utf-8', errors='replace')}",
                })
                names.append(path.name)
            elif ext in self.IMAGE_EXTS:
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

    def analyze(self) -> tuple[Optional[ConversationAnalysis], list[str]]:
        """Run the analysis pass over uploads/. Returns (analysis, filenames)."""
        blocks, names = self.load_blocks()
        if not blocks:
            return None, []
        content = ([{"type": "text",
                     "text": self.INSTRUCTIONS + self.parser.get_format_instructions()}]
                   + blocks)
        reply = self.llm.invoke([HumanMessage(content=content)])
        return self.parser.parse(_blocks_to_text(reply.content)), names

    @staticmethod
    def to_prompt_text(analysis: ConversationAnalysis) -> str:
        """Format the analysis for injection into the interviewer's system prompt."""
        def bullet(items):
            return "\n".join(f"  - {i}" for i in items) or "  (none)"
        return (
            f"Inquiry categories observed:\n{bullet(analysis.inquiry_categories)}\n"
            f"Resolved patterns (already learnable, do not ask):\n{bullet(analysis.resolved_patterns)}\n"
            f"KNOWLEDGE GAPS (ask about these, one at a time, citing the example):\n{bullet(analysis.knowledge_gaps)}\n"
            f"NOTABLE INCIDENTS (raise these live, quoting the date/detail, and ask "
            f"whether they are typical):\n{bullet(analysis.notable_incidents)}\n"
            f"Facts learned (fill the form with these, do not ask):\n{bullet(analysis.facts_learned)}"
        )


class RequirementsBot:
    """The requirements-gathering interviewer. Holds one interview's state."""

    GREETING = ("Hi! I help businesses figure out exactly what they need from a "
                "chatbot. Before we start — what's your name?")
    NO_MATERIALS = "No materials shared yet."
    # The interviewer's system prompt lives in its own file so it can be
    # edited and improved without touching this code.
    PROMPT_FILE = ROOT / "interviewer_prompt.txt"

    ANALYSIS_DONE_NOTE = (
        "(System note: the analysis of the shared materials is now in your "
        "instructions. React to it: mention 1-2 useful things you learned, "
        "then ask about the first knowledge gap or notable incident, quoting "
        "the specific dated example you saw.)")
    ANALYSIS_EMPTY_NOTE = (
        "(System note: the uploads folder is empty — no readable files. "
        "Gently tell the owner nothing was found and how to add files, "
        "then continue the interview.)")

    def __init__(self, model: str = "claude-sonnet-5",
                 uploads_dir: Path = ROOT / "uploads"):
        # Explicit max_tokens: the interviewer re-emits the FULL form as JSON
        # every turn, so replies grow throughout the interview and must never
        # be truncated. 8000 proved too small once a talkative owner filled
        # the richer form (truncated JSON -> parse failure on every retry).
        self.llm = ChatAnthropic(model=model, max_tokens=16000)
        self.uploads_dir = uploads_dir
        self.analyzer = MaterialsAnalyzer(self.llm, uploads_dir)
        self.parser = PydanticOutputParser(pydantic_object=InterviewTurn)
        # --- interview state ---
        # The greeting starts the history so the model knows it already asked
        # for the owner's name.
        self.chat_history: list[tuple[str, str]] = [("ai", self.GREETING)]
        self.analysis: Optional[ConversationAnalysis] = None
        self.analysis_text: str = self.NO_MATERIALS
        self.complete: bool = False
        # Token accounting across the whole interview (cache_read tokens are
        # billed at 10% of the fresh-input price).
        self.usage = {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}

    # ---------------- public API ----------------
    def send(self, message: str) -> tuple[list[str], InterviewTurn]:
        """One owner message in, the bot's reply (or replies) out.

        Returns (messages_to_show, final_turn). Two messages come back when
        the bot paused to analyze the owner's uploaded materials."""
        messages = []
        turn = self._ask(message)
        messages.append(turn.next_message)

        # The bot decided the owner's files are ready: analyze, then react.
        if turn.run_file_analysis:
            analysis, _names = self.analyzer.analyze()
            if analysis is None:
                note = self.ANALYSIS_EMPTY_NOTE
            else:
                self.analysis = analysis
                self.analysis_text = MaterialsAnalyzer.to_prompt_text(analysis)
                note = self.ANALYSIS_DONE_NOTE
            turn = self._ask(note, record_as="(files were analyzed)")
            messages.append(turn.next_message)

        self.complete = turn.interview_complete
        return messages, turn

    def brief(self, turn: InterviewTurn) -> dict:
        """The deliverable: the form PLUS the raw materials analysis (booking
        flows, resolved patterns etc. are gold for whoever builds the bot)."""
        return {
            "requirements": turn.requirements.model_dump(),
            "conversation_analysis": self.analysis.model_dump() if self.analysis else None,
        }

    # ---------------- state (de)serialization ----------------
    def to_dict(self) -> dict:
        return {
            "chat_history": [list(pair) for pair in self.chat_history],
            "analysis": self.analysis.model_dump() if self.analysis else None,
            "analysis_text": self.analysis_text,
            "complete": self.complete,
            "usage": self.usage,
        }

    @classmethod
    def from_dict(cls, state: dict, **kwargs) -> "RequirementsBot":
        bot = cls(**kwargs)
        bot.chat_history = [tuple(pair) for pair in state["chat_history"]]
        if state["analysis"]:
            bot.analysis = ConversationAnalysis(**state["analysis"])
        bot.analysis_text = state["analysis_text"]
        bot.complete = state["complete"]
        bot.usage = state.get("usage", bot.usage)  # older sessions lack it
        return bot

    # ---------------- internals ----------------
    def _build_messages(self, question: str) -> list:
        """Build the turn's messages with Anthropic prompt-cache breakpoints:
        one on the system prompt and one on the last history message, so each
        turn only pays full input price for what's new since the previous
        turn. The prompt file is read FRESH so edits take effect on the very
        next turn, not on the next process restart."""
        system_text = (self.PROMPT_FILE.read_text(encoding="utf-8")
                       .replace("{analysis}", self.analysis_text)
                       .replace("{format_instructions}",
                                self.parser.get_format_instructions()))
        cached = {"cache_control": {"type": "ephemeral"}}
        messages = [SystemMessage(
            content=[{"type": "text", "text": system_text, **cached}])]
        for i, (role, text) in enumerate(self.chat_history):
            # Moving breakpoint: cache the whole conversation prefix.
            content = ([{"type": "text", "text": text, **cached}]
                       if i == len(self.chat_history) - 1 else text)
            cls = HumanMessage if role == "human" else AIMessage
            messages.append(cls(content=content))
        messages.append(HumanMessage(content=question))
        return messages

    def _ask(self, question: str, record_as: Optional[str] = None) -> InterviewTurn:
        """One model turn, appended to history. Retries because the model
        occasionally returns an empty/unparseable reply (e.g. a thinking-only
        response); one bad turn must not kill the interview."""
        last_error = None
        for _ in range(3):
            reply = self.llm.invoke(self._build_messages(question))
            u = reply.response_metadata.get("usage") or {}
            self.usage["fresh_in"] += u.get("input_tokens") or 0
            self.usage["cache_read"] += u.get("cache_read_input_tokens") or 0
            self.usage["cache_write"] += u.get("cache_creation_input_tokens") or 0
            self.usage["out"] += u.get("output_tokens") or 0
            output = _blocks_to_text(reply.content)
            try:
                turn = self.parser.parse(output)
            except Exception as e:
                last_error = e
                continue
            # Guard: the model sometimes flags completion mid-interview. A real
            # completion follows a confirmed summary, by which point the core
            # fields below are always filled — refuse the flag until they are.
            # (Only the two essentials: requiring more can trap the bot in an
            # endless goodbye loop when an owner disengages early — anything
            # else missing belongs in open_items.)
            r = turn.requirements
            if turn.interview_complete and not (r.problem_to_solve and r.channels):
                turn.interview_complete = False
            self.chat_history.append(("human", record_as or question))
            self.chat_history.append(("ai", output))
            return turn
        raise last_error


def _blocks_to_text(content) -> str:
    """Claude replies with a list of content blocks; keep only the text ones."""
    if isinstance(content, list):
        return "".join(block["text"] for block in content if block["type"] == "text")
    return content
