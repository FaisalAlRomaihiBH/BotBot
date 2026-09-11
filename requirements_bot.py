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

from models import (BusinessRequirements, ConversationAnalysis, InterviewTurn,
                    UNKNOWN_VARIES)

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
    ESSENTIALS_SWEEP_NOTE = (
        "(System note: you flagged the interview complete, but these essentials "
        "were never asked: {missing}. Your closing message was NOT sent to the "
        "owner. Ask for them NOW in ONE short rapid-fire message — one compact "
        "question per missing item, no preamble, making clear it is the last "
        "thing you need before you write this up. Do not re-summarize. If they "
        "decline or answer vaguely, record that in the field itself and close "
        "out on your next turn: this is the only time you will be sent back.)")

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
        # Whether the completion gate has already forced its one rapid-fire
        # sweep for unasked essentials. Exactly one: a second refusal to accept
        # the owner's goodbye is how a wrap-up becomes an endless loop.
        self.essentials_swept: bool = False
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

        # COMPLETION GATE. A brief that never asked about money, dates or scope
        # is not a finished interview, whatever the model decided — those three
        # were still null in wrap-ups that read as thorough. The premature
        # sign-off is withheld and one rapid-fire sweep is forced in its place;
        # after that the interview may end whether or not the owner answered,
        # because an asked-and-refused essential is a recorded fact and grinding
        # on it costs the goodbye.
        missing = _missing_essentials(turn.requirements)
        if turn.interview_complete and missing and not self.essentials_swept:
            self.essentials_swept = True
            turn.interview_complete = False
            messages.pop()
            turn = self._ask(
                self.ESSENTIALS_SWEEP_NOTE.format(missing=", ".join(missing)),
                record_as="(essentials sweep)")
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
            "essentials_swept": self.essentials_swept,
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
        # Older sessions predate the completion gate: they get their one sweep.
        bot.essentials_swept = state.get("essentials_swept", False)
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

        # Legacy pointer list: consumers reading the old single list still find
        # the feelings a builder must act on, but they find a REFERENCE to
        # adoption_risks rather than a second copy of it — mirroring the text
        # verbatim put the same six sentences in both fields. Anything the model
        # wrote here despite the prompt is real signal, so it is promoted into
        # adoption_risks first instead of being dropped. background_color is
        # still not pointed at: "founded in 1978 by his father" is not a
        # sentiment.
        written = [i for i in (requirements.owner_sentiment_or_concerns or [])
                   if i not in _SENTINELS and not _REF_LABEL_RE.match(i)]
        if written:
            requirements.adoption_risks = merge(requirements.adoption_risks, written)
        requirements.owner_sentiment_or_concerns = [
            _ref("see adoption_risks", n, risk)
            for n, risk in enumerate(requirements.adoption_risks or [], 1)] or None
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
            gaps = []
            if not offer.lead_time:
                if any(w in f"{offer.name} {trigger}".lower() for w in _RUSH_WORDS):
                    gaps.append(
                        f"lead time/turnaround for '{offer.name}'{applies} — a rush "
                        f"price was recorded without the turnaround it buys, so the "
                        f"bot cannot say what the extra money gets the customer")
                elif complete:
                    # Every persona run shipped a catalog with no turnaround on
                    # any line, and nothing said so. For repair and
                    # made-to-order work "how long will it take?" is the most
                    # common question the bot will receive, so a null here is
                    # named as unasked rather than shipped as silence.
                    gaps.append(
                        f"turnaround/lead time for '{offer.name}' — never recorded; "
                        f"ask how long a customer waits for this line (and use "
                        f"'{UNKNOWN_VARIES} — <reason>' if it genuinely varies)")
            if trigger and _price_amounts_missing(offer):
                gaps.append(
                    f"price variants for '{offer.name}'{applies} — the price is "
                    f"conditional but the amount for each case was never stated")
            if gaps:
                requirements.unresolved_business_facts = merge(
                    requirements.unresolved_business_facts, gaps)

        # The same gap one level up: not one duration recorded for a business
        # that has services at all.
        if (complete and requirements.services_and_pricing
                and not requirements.service_durations):
            requirements.unresolved_business_facts = merge(
                requirements.unresolved_business_facts, [DURATIONS_GAP])

        # A budget in the owner's words only ("small money if simple") is not
        # something anyone can scope against, so it ships as a required
        # follow-up instead of a filled field. An owner who was asked and
        # declined is not chased again.
        if complete and "refus" not in (requirements.budget_confidence or "").lower() \
                and not (requirements.budget_amount_min
                         or requirements.budget_amount_max):
            requirements.unresolved_business_facts = merge(
                requirements.unresolved_business_facts,
                [BUDGET_FIGURE_GAP.format(words=f"'{requirements.budget}'")
                 if requirements.budget else BUDGET_GAP])

        # open_items is a REFERENCE INDEX, not a bucket — rebuilt from the
        # routed lists so nothing is invisible just because the model picked the
        # wrong one. Each entry POINTS at the field and position that holds the
        # full wording instead of repeating it: copying the text meant the three
        # routed lists and open_items shipped the same sentences twice over, and
        # a reader could not tell a second item from a second copy.
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
            for position, item in enumerate(items or [], 1):
                if _norm(item) in seen:
                    continue
                seen.add(_norm(item))
                if item not in _SENTINELS:
                    index.append(_ref(label, position, item))
        # Whatever the model put straight into open_items has no field to point
        # at, so it keeps its own text. Our labels are stripped first, so
        # rebuilding an index we built before re-files entries instead of
        # nesting the tags.
        for item in (_unlabel(i) for i in (requirements.open_items or [])):
            key = _norm(item)
            # An index entry we built on an earlier turn is an EXCERPT of a
            # routed item, so a prefix match counts as already indexed — else
            # every truncated reference reappears as a new "unfiled" item.
            if not key or any(known.startswith(key) for known in seen):
                continue
            seen.add(key)
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


# Any label this module puts at the head of a reference entry, with or without
# its "#n" position: "[owner fact #2] ", "[bot decision #1] ", "[customer reply
# owed #3] ", "[see adoption_risks #1] ", "[unfiled] ".
# A model-written provenance tag ("[source: uploaded_materials, ...]") does not
# match — the colon stops it — so stripping a label never eats a real one.
_REF_LABEL_RE = re.compile(r"^\[(?:see\s+)?[a-z][a-z_ ]*(?:\s*#\d+)?\]\s*")
_REF_WIDTH = 72

# Provenance tag on every fact the materials analysis produced. It is a lead
# until the owner confirms it out loud, never an established fact.
UPLOAD_TAG = "[source: uploaded_materials, verified: false]"
UPLOAD_TAG_VERIFIED = "[source: uploaded_materials, verified: true]"
# Every shape the tag comes back in — the model appends a second one to the END
# of a line it re-emits, and sometimes writes a bare "(unverified)" instead.
# Matching only a leading tag left those inside the fact text, which made one
# fact look like two and tagged it both ways at once.
_UPLOAD_TAG_RE = re.compile(r"\[source:\s*uploaded_materials[^\]]*\]", re.I)
_VERIFIED_WORD_RE = re.compile(r"verified\s*[:=]\s*true", re.I)
_VERIFIED_SUFFIX_RE = re.compile(r"\s*\(\s*(un)?verified\s*\)\s*$", re.I)

# Explicit statements of absence, so a null can never be read as "we asked and
# the answer was none".
NO_REPLIES_OWED = ("none — the analyzed materials contained no customer message "
                   "left without an answer")
NO_SENTIMENT = ("none captured — the owner voiced no concerns, worries or "
                "reservations on record during this interview")
SENTIMENT_GAP = ("the owner's own concerns/anxieties about the bot were never "
                 "captured — ask before the build starts")
_SENTINELS = frozenset({NO_REPLIES_OWED, NO_SENTIMENT})

# Gaps the closing turn must state out loud. Every persona run shipped a brief
# whose durations and budget were simply null, with nothing recording that the
# questions had gone unasked — a silent null reads as "no constraint".
DURATIONS_GAP = ("how long each service takes and its turnaround — no duration was "
                 "recorded for any line; ask per service before the build")
BUDGET_GAP = ("budget was never captured — ask for a figure the owner would not "
              "want to go past, plus the currency, before scoping")
BUDGET_FIGURE_GAP = ("budget is only recorded in words ({words}) — a numeric range "
                     "and currency are still needed before scoping")

# The legacy separator that used to join two phrasings of one customer's request
# into a single entry. It is no longer written — a "| also recorded as:" line was
# a visible merge artifact, not a to-do — but incoming text is still split on it
# so the two halves get reconciled properly.
_ALSO_RECORDED = " | also recorded as: "

# Words that mark a price as conditional, so it is meaningless without the
# turnaround it buys.
_RUSH_WORDS = ("rush", "urgent", "express", "expedite", "surcharge", "same-day",
               "last-minute", "last minute")

# Wordings that mean the owner could not put a number on it. A price carrying one
# of these is genuinely unstated; "$12/person" is not, whatever conditions hang
# off it. Whole words only — a substring match reads "$5 per basket" as "ask".
_VAGUE_PRICE_RE = re.compile(
    r"\b(?:depends?|vary|varies|variable|tbd|tbc|to be confirmed|unknown|"
    r"case[ -]by[ -]case|quote|quoted|ask|negotiable)\b", re.I)


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
    """(verified?, the bare fact) behind its provenance tags — ALL of them,
    wherever they sit.

    The model re-emits a seeded fact with a second tag appended at the end, so
    one line arrived as "[... verified: false] 2 tacos cost $5 [... verified:
    true]": two contradictory flags, and a fact whose text no longer matched the
    same fact tagged once. Every tag is stripped and the flags are OR-ed, so the
    line collapses onto its twin and ships with one flag. An untagged entry
    counts as unverified — the safe direction, since the tag is what stops a lead
    from being quoted to a customer as fact."""
    verified = bool(_VERIFIED_WORD_RE.search(" ".join(_UPLOAD_TAG_RE.findall(item))))
    fact = _UPLOAD_TAG_RE.sub(" ", item)
    # "... (unverified)" / "... (verified)" — the tag written out longhand.
    suffix = _VERIFIED_SUFFIX_RE.search(fact)
    if suffix:
        verified = verified or not suffix.group(1)
        fact = fact[:suffix.start()]
    return verified, " ".join(fact.split())


def _same_request(a: str, b: str) -> bool:
    """Two phrasings of ONE request, rather than two requests from one customer.

    Half the words of the shorter entry appearing in the longer means the
    analyzer's verbatim line and the model's re-worded line are the same waiting
    customer; below that, one person really did ask for two things and both must
    survive as their own to-do."""
    words_a, words_b = set(_norm(a).split()), set(_norm(b).split())
    shortest = min(words_a, words_b, key=len)
    return bool(shortest) and len(words_a & words_b) / len(shortest) >= 0.5


def _ref(label: str, position: int, item: str) -> str:
    """A POINTER to an entry that lives in full in another field.

    The index and the legacy sentiment mirror used to carry the whole sentence,
    so one piece of work was readable verbatim in three fields and no reader
    could tell which one owned it. An excerpt long enough to recognise, plus the
    position it sits at, keeps the index usable without copying the text."""
    body = " ".join(item.split())
    if len(body) > _REF_WIDTH:
        body = body[:_REF_WIDTH].rstrip() + "..."
    return f"[{label} #{position}] {body}"


def _price_amounts_missing(offer) -> bool:
    """Does this conditional entry's price still need an amount from the owner?

    Only a price nobody could state counts: no price at all, a band whose two
    ends were never separated, or a "depends on the design" answer. A fully
    stated figure does NOT, however many conditions hang off it — treating any
    filled fee_trigger_condition as a missing amount is what invented "price
    variants for 'Catering (per person)' — the amount for each case was never
    stated" for a line priced at $12/person."""
    price = (offer.price or "").strip()
    if not price:
        return True
    if offer.price_min and offer.price_max:
        return False                       # both ends of the band are on record
    if (offer.price_basis or "").strip().lower() == "range":
        return True                        # a band missing one or both bounds
    if re.search(r"\d\s*(?:-|–|—|to)\s*\d", price):
        return True                        # "45-90/hr" never split into min/max
    return bool(_VAGUE_PRICE_RE.search(price))


def _missing_essentials(requirements: BusinessRequirements) -> list[str]:
    """Which of the three scoping essentials were never ASKED.

    A value of any kind counts as asked — "unknown-varies", "unknown - pending
    confirmation", a flat refusal. Only a null means the question never reached
    the owner, and a brief with no money, no date and no scope decision cannot
    be quoted, scheduled or built, whatever else it holds."""
    asked = {
        "budget": (requirements.budget or requirements.budget_amount_min
                   or requirements.budget_amount_max
                   or requirements.budget_confidence),
        "timeline": requirements.timeline,
        "solution_scope": requirements.solution_scope,
    }
    return [field for field, value in asked.items() if not (value or "").strip()]


def _merge_replies(existing: Optional[list[str]],
                   incoming: list[str]) -> Optional[list[str]]:
    """One entry per outstanding REQUEST, reconciled across its phrasings.

    The analyzer's verbatim entry and the model's re-worded one describe the
    same waiting person twice, and exact-string merging kept both, so a single
    hanging customer became two items on the delivery team's list. Concatenating
    them instead ("... | also recorded as: ...") only moved the duplication
    inside the line. So the two are reconciled into the fullest single phrasing —
    and a second, genuinely different request from the same customer still gets
    its own entry, because dropping it would lose a real person waiting on a real
    answer."""
    requests: dict[str, list[str]] = {}
    for item in (part.strip()
                 for raw in list(existing or []) + list(incoming)
                 for part in raw.split(_ALSO_RECORDED)):
        if not item:
            continue
        kept = requests.setdefault(_reply_key(item), [])
        for i, other in enumerate(kept):
            if _same_request(item, other):
                kept[i] = max(other, item, key=len)   # fullest phrasing wins
                break
        else:
            kept.append(item)
    return [item for kept in requests.values() for item in kept] or None


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
    """Remove an index/reference label, so the index can be rebuilt safely.

    Labels now carry a position ("[owner fact #2] "), so the older bare form is
    stripped too — otherwise rebuilding an index nests the tags."""
    return _REF_LABEL_RE.sub("", item, count=1)


def _blocks_to_text(content) -> str:
    """Claude replies with a list of content blocks; keep only the text ones."""
    if isinstance(content, list):
        return "".join(block["text"] for block in content if block["type"] == "text")
    return content
