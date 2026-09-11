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
import re
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
- facts_learned: hard facts about the business DIRECTLY VISIBLE in the materials
  (hours, services, prices, languages used...). Only what is stated in the
  material itself — never an inference from the material's own form. A WhatsApp
  export proves the owner exported a WhatsApp chat; it does NOT establish which
  channels the business uses or wants, so never write a channel, a volume or a
  team size that nobody actually stated. Everything here reaches the interviewer
  flagged as unverified and gets checked with the owner, so a guess costs a turn.
- notable_incidents: specific single events worth asking the owner about, each
  written as "<date/time from the material> — <what happened> (quote: "...")".
  Include long waits before a reply, complaints, apologies, lost or abandoned
  customers, unanswered messages, and promises made. Use the timestamps exactly
  as they appear in the material; if a message has no timestamp, say
  "(no timestamp)" rather than inventing one. These get raised verbatim in the
  live interview, so they must be quotable.
- open_customer_requests: every customer whose concrete request is still
  unanswered at the end of the material — one entry each, as
  "<who/when> — <what they asked> (quote: "...")". List them even when the
  same thread also appears under notable_incidents: these become a to-do list
  of replies the owner owes, so a missed one is a real customer lost.

Wrap your entire output in this format and provide no other text
"""

    def __init__(self, llm, uploads_dir: Path):
        self.llm = llm
        self.uploads_dir = uploads_dir
        self.parser = PydanticOutputParser(pydantic_object=ConversationAnalysis)

    def material_paths(self) -> list[Path]:
        """The readable files in uploads/, in a stable order. Cheap — no file
        contents are read, so a caller can poll it every turn to spot changes."""
        if not self.uploads_dir.exists():
            return []
        return [p for p in sorted(self.uploads_dir.iterdir())
                if p.suffix.lower() in self.TEXT_EXTS | self.IMAGE_EXTS]

    def load_blocks(self) -> tuple[list, list[str]]:
        """Read everything in uploads/ into Claude content blocks (text + images).

        Returns (blocks, loaded_filenames)."""
        blocks, names = [], []
        for path in self.material_paths():
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

    def analyze(self, attempts: int = 3) -> tuple[Optional[ConversationAnalysis], list[str]]:
        """Run the analysis pass over uploads/. Returns (analysis, filenames).

        (None, []) means there was nothing readable to analyze — the ONLY
        legitimate way to come back empty. A model call that returns something
        unparseable is retried instead, and raises if it never parses, because
        interviewing blind over materials the owner did share is worse than
        stopping."""
        blocks, names = self.load_blocks()
        if not blocks:
            return None, []
        content = ([{"type": "text",
                     "text": self.INSTRUCTIONS + self.parser.get_format_instructions()}]
                   + blocks)
        last_error = None
        for _ in range(attempts):
            reply = self.llm.invoke([HumanMessage(content=content)])
            try:
                return self.parser.parse(_blocks_to_text(reply.content)), names
            except Exception as e:
                last_error = e
        raise RuntimeError(
            f"materials analysis failed for {names} after {attempts} attempts"
        ) from last_error

    @staticmethod
    def to_prompt_text(analysis: ConversationAnalysis) -> str:
        """Format the analysis for injection into the interviewer's system prompt."""
        def bullet(items):
            return "\n".join(f"  - {i}" for i in items) or "  (none)"
        return (
            f"Inquiry categories observed:\n{bullet(analysis.inquiry_categories)}\n"
            f"Resolved patterns (already learnable, do not ask):\n{bullet(analysis.resolved_patterns)}\n"
            f"KNOWLEDGE GAPS (ask about these, one at a time, citing the example):\n{bullet(analysis.knowledge_gaps)}\n"
            f"CUSTOMERS STILL OWED A REPLY (already recorded in "
            f"customer_replies_owed — confirm them with the owner):\n{bullet(analysis.open_customer_requests)}\n"
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
    ANALYSIS_UNCHANGED_NOTE = (
        "(System note: those files were already analyzed and their findings "
        "are in your instructions — nothing new was added. Say so briefly and "
        "continue with the next knowledge gap or notable incident.)")

    def __init__(self, model: str = "claude-sonnet-5",
                 uploads_dir: Path = ROOT / "uploads"):
        self.model = model
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
        # Which files the current analysis covers. Drives the auto-scan: the
        # folder decides what gets ingested, never the owner's say-so.
        self.analyzed_files: list[str] = []
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
        # Ingest whatever is in uploads/ BEFORE asking anything. Owners say "I
        # have nothing" while a full message log sits in the folder; that used
        # to drop the entire log. The folder is the trigger, not the owner.
        self._scan_materials()
        turn = self._ask(message)
        messages.append(turn.next_message)

        # The bot decided the owner's files are ready: re-scan, then react.
        if turn.run_file_analysis:
            note = {"analyzed": self.ANALYSIS_DONE_NOTE,
                    "unchanged": self.ANALYSIS_UNCHANGED_NOTE,
                    "empty": self.ANALYSIS_EMPTY_NOTE}[self._scan_materials()]
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
            "analyzed_files": self.analyzed_files,
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
        bot.analyzed_files = state.get("analyzed_files", [])  # older sessions lack it
        bot.complete = state["complete"]
        bot.usage = state.get("usage", bot.usage)  # older sessions lack it
        return bot

    # ---------------- internals ----------------
    def _scan_materials(self) -> str:
        """Analyze uploads/ if its contents changed since the last analysis.

        Returns "analyzed", "unchanged" or "empty". Only the file NAMES are
        listed on the common path — nothing is read and no model call is made
        unless the file set actually differs, so running this before every
        turn is free."""
        names = [p.name for p in self.analyzer.material_paths()]
        if not names:
            return "empty"
        if names == self.analyzed_files and self.analysis is not None:
            return "unchanged"
        analysis, names = self.analyzer.analyze()
        if analysis is None:
            # Files exist, yet no analysis object came back. Reporting "empty"
            # here is what let an interview run blind over materials the owner
            # had shared, and ship a brief that looked complete. Fail loudly.
            raise RuntimeError(
                f"uploaded materials are present but produced no analysis: {names}")
        self.analysis = analysis
        self.analysis_text = MaterialsAnalyzer.to_prompt_text(analysis)
        self.analyzed_files = names
        return "analyzed"

    def _postprocess(self, requirements: BusinessRequirements,
                     complete: bool = False) -> None:
        """Fix up the form in code, in place, for the things the model must not
        be trusted to get right on its own.

        Safe to run every turn: the history keeps the model's RAW output, so
        these additions never feed back into the next turn and compound."""
        def merge(existing: Optional[list[str]], extra: list[str]) -> Optional[list[str]]:
            return _dedupe(list(existing or []) + list(extra)) or None

        if self.analysis:
            # Material-derived facts get their own home instead of being
            # indistinguishable from what the owner said out loud — and a
            # provenance tag, because the analyzer does occasionally assert
            # something the material never said (it once "learned" the
            # business's channel from the export format). Tagged facts are
            # leads to confirm, not established facts: the prompt forbids
            # promoting one into a normal field before the owner verifies it.
            requirements.facts_from_uploads = _merge_upload_facts(
                requirements.facts_from_uploads, self.analysis.facts_learned)
            # A customer left hanging in the log is a fact about the business,
            # not something to wait for the owner to volunteer.
            requirements.customer_replies_owed = _merge_replies(
                requirements.customer_replies_owed,
                self.analysis.open_customer_requests)
            # ...and "no replies owed" must be a statement, never a silence:
            # materials analyzed + an empty list is nearly always a real
            # customer dropped on the floor.
            if complete and not requirements.customer_replies_owed:
                requirements.customer_replies_owed = [NO_REPLIES_OWED]

        # Legacy mirror: consumers reading the old single list still see the
        # feelings a builder must act on. background_color is deliberately NOT
        # mirrored — copying it forward filled this field with verbatim
        # duplicates of other fields, and "the business was founded in 1978 by
        # his father" is not a sentiment. Anecdote stays in background_color.
        requirements.owner_sentiment_or_concerns = merge(
            requirements.owner_sentiment_or_concerns,
            list(requirements.adoption_risks or []))
        # Shipping this null hides whether the owner was asked at all. On the
        # final turn, say which it was — and if nothing was captured, that is
        # a gap the delivery team must close before the build.
        if complete and not requirements.owner_sentiment_or_concerns:
            requirements.owner_sentiment_or_concerns = [NO_SENTIMENT]
            requirements.unresolved_business_facts = merge(
                requirements.unresolved_business_facts, [SENTIMENT_GAP])

        # A conditional price the bot cannot actually apply is an owner
        # follow-up, not a priced service. Two different gaps, two different
        # sentences: a rush price is missing its TURNAROUND, an ordinary
        # "depends on..." price is missing its AMOUNTS. Emitting the surcharge
        # wording for both produced lines like "lead time/turnaround for
        # 'Burrito' (price depends on fillings) — a surcharge was recorded
        # without the turnaround that triggers it", which describes nothing.
        for offer in requirements.services_and_pricing or []:
            if isinstance(offer, str):
                continue
            trigger = offer.fee_trigger_condition or ""
            applies = f" (applies when: {trigger})" if trigger else ""
            gap = None
            if (any(w in f"{offer.name} {trigger}".lower() for w in _RUSH_WORDS)
                    and not offer.lead_time):
                gap = (f"lead time/turnaround for '{offer.name}'{applies} — a rush "
                       f"price was recorded without the turnaround it buys, so the "
                       f"bot cannot say what the extra money gets the customer")
            elif trigger and not (offer.price_min and offer.price_max):
                gap = (f"price variants for '{offer.name}'{applies} — the price is "
                       f"conditional but the amount for each case was never stated")
            if gap:
                requirements.unresolved_business_facts = merge(
                    requirements.unresolved_business_facts, [gap])

        # open_items is an INDEX, not a bucket — rebuilt from the routed lists
        # so nothing is invisible just because the model picked the wrong one.
        # Sentinels are deliberate statements of absence, not work: they stay
        # in their own field and out of the delivery team's to-do list.
        # An item routed into two lists, or re-worded between turns, is still
        # ONE piece of work, so the index dedupes on the normalized text rather
        # than the exact string.
        routed = [("owner fact", requirements.unresolved_business_facts),
                  ("bot decision", requirements.pending_design_decisions),
                  ("customer reply owed", requirements.customer_replies_owed)]
        index: list[str] = []
        seen: set[str] = set()
        for label, items in routed:
            for item in items or []:
                if _norm(item) in seen:
                    continue
                seen.add(_norm(item))
                if item not in _SENTINELS:
                    index.append(f"[{label}] {item}")
        # Strip our own labels off whatever is already there, so rebuilding an
        # index we built before re-files entries instead of nesting the tags.
        for item in (_unlabel(i) for i in (requirements.open_items or [])):
            if _norm(item) in seen:
                continue
            seen.add(_norm(item))
            index.append(f"[unfiled] {item}")
        requirements.open_items = index or None

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
        # Hard gate: never ask a question over materials we failed to read. The
        # analysis reaches the model only through analysis_text, so an analysis
        # that is missing here means the interviewer is working blind.
        if self.analyzer.material_paths() and (
                self.analysis is None or self.analysis_text == self.NO_MATERIALS):
            raise RuntimeError(
                "refusing to interview: uploaded materials have not been analyzed "
                "into the interviewer's context")
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
            self._postprocess(r, complete=turn.interview_complete)
            self.chat_history.append(("human", record_as or question))
            self.chat_history.append(("ai", output))
            return turn
        raise last_error


_OPEN_ITEM_LABELS = ("owner fact", "bot decision", "customer reply owed", "unfiled")

# Provenance tag on every fact the materials analysis produced. It is a lead
# until the owner confirms it out loud, never an established fact.
UPLOAD_TAG = "[source: uploaded_materials, verified: false]"
UPLOAD_TAG_VERIFIED = "[source: uploaded_materials, verified: true]"
_UPLOAD_TAG_PREFIX = "[source: uploaded_materials"

# Explicit statements of absence, so a null can never be read as "we asked and
# the answer was none".
NO_REPLIES_OWED = ("none — the analyzed materials contained no customer message "
                   "left without an answer")
NO_SENTIMENT = ("none captured — the owner voiced no concerns, worries or "
                "reservations on record during this interview")
SENTIMENT_GAP = ("the owner's own concerns/anxieties about the bot were never "
                 "captured — ask before the build starts")
_SENTINELS = frozenset({NO_REPLIES_OWED, NO_SENTIMENT})

# Joins two phrasings of one customer's outstanding request into a single
# to-do item, so deduplicating never costs the delivery team a detail.
_ALSO_RECORDED = " | also recorded as: "

# Words that mark a price as conditional, so it is meaningless without the
# turnaround it buys.
_RUSH_WORDS = ("rush", "urgent", "express", "expedite", "surcharge", "same-day",
               "last-minute", "last minute")


def _norm(text: str) -> str:
    """A comparison key for a list entry: case, punctuation and spacing dropped.

    Exact-string equality is what let the same fact sit in a list twice under
    two spellings; this collapses them without touching the text that ships."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _reply_key(item: str) -> str:
    """WHICH CUSTOMER an owed-reply entry is about.

    Entries are written "<who/when> — <what they asked> (quote: ...)", and the
    same request reaches us twice: once verbatim from the analyzer, once
    re-phrased by the model. Nothing about the sentence is stable — not even
    the date, which comes back as "14 March" one turn and "14 Mar" the next —
    so identity is the name at the head, up to the first punctuation and at
    most four words of it."""
    head = re.split(r"[,(;:]|\s+[—–-]\s+", item, maxsplit=1)[0]
    return " ".join(_norm(head).split()[:4])


def _dedupe(items: list[str], key=_norm) -> list[str]:
    """One entry per key, in first-seen order, keeping the fullest phrasing."""
    best: dict[str, str] = {}
    for item in items:
        k = key(item)
        if k not in best or len(item) > len(best[k]):
            best[k] = item
    return list(best.values())


def _split_upload_tag(item: str) -> tuple[bool, str]:
    """(verified?, the bare fact) behind a provenance tag. An untagged entry
    counts as unverified — the safe direction, since the tag is what stops a
    lead from being quoted to a customer as fact."""
    if item.startswith(_UPLOAD_TAG_PREFIX) and "]" in item:
        tag, fact = item.split("]", 1)
        return "verified: true" in tag.lower(), fact.strip()
    return False, item.strip()


def _merge_replies(existing: Optional[list[str]],
                   incoming: list[str]) -> Optional[list[str]]:
    """One entry per waiting CUSTOMER — but keeping every phrasing of what they
    asked for.

    The analyzer's verbatim entry and the model's re-worded one describe the
    same person twice, and exact-string merging kept both, so a single hanging
    customer became two items on the delivery team's list. The extra phrasing
    is APPENDED rather than dropped: if the two really are two separate
    requests from one customer, silently keeping only the longer sentence
    would lose a real person waiting on a real answer."""
    replies: dict[str, str] = {}
    for item in (i.strip() for i in list(existing or []) + list(incoming)):
        kept = replies.get(_reply_key(item))
        if kept is None:
            replies[_reply_key(item)] = item
        elif _norm(item) not in _norm(kept):
            replies[_reply_key(item)] = f"{kept}{_ALSO_RECORDED}{item}"
    return list(replies.values()) or None


def _merge_upload_facts(existing: Optional[list[str]],
                        learned: list[str]) -> Optional[list[str]]:
    """Seed the analyzer's facts into facts_from_uploads — ONE entry per fact,
    carrying ONE verification flag.

    The model re-emits a seeded fact in its own words, and re-tags it
    "verified: true" once the owner confirms it, while the analyzer keeps
    handing us the verbatim original. Matching on the exact string saw two
    different strings and kept both, so every fact appeared twice, at once
    verified and unverified. Facts are matched on their bare text instead, and
    a "verified: true" anywhere wins: within one interview, confirmation by the
    owner is a one-way door."""
    facts: dict[str, tuple[bool, str]] = {}
    for item in list(existing or []) + list(learned):
        verified, fact = _split_upload_tag(item)
        was_verified, kept = facts.get(_norm(fact), (False, fact))
        facts[_norm(fact)] = (was_verified or verified,
                              max(kept, fact, key=len))
    return [f"{UPLOAD_TAG_VERIFIED if verified else UPLOAD_TAG} {fact}"
            for verified, fact in facts.values()] or None


def _unlabel(item: str) -> str:
    """Remove an open_items index label, so the index can be rebuilt safely."""
    for label in _OPEN_ITEM_LABELS:
        if item.startswith(f"[{label}] "):
            return item[len(label) + 3:]
    return item


def _blocks_to_text(content) -> str:
    """Claude replies with a list of content blocks; keep only the text ones."""
    if isinstance(content, list):
        return "".join(block["text"] for block in content if block["type"] == "text")
    return content
