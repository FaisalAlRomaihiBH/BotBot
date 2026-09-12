# BotBot — Project Handoff

_Generated 2026-09-12. Everything below is the full source of the project, preceded by a summary._

## What this project is

**RequirementsBot** — an AI discovery interviewer for businesses that want a chatbot built. It chats with a business owner (like a consultant would), fills in a large structured requirements form as it learns facts, analyzes the owner's real customer materials (WhatsApp exports, screenshots dropped into `uploads/`), and saves a complete build-ready brief to `requirements_brief.json`.

Stack: Python, LangChain + `langchain-anthropic` (Claude Sonnet 5 by default), Pydantic schemas, prompt caching for cost control.

## Components

| File | Role |
|---|---|
| `main.py` | Interactive CLI chat loop; saves the brief (partial briefs too) |
| `models.py` | All Pydantic schemas: `BusinessRequirements` (~70 fields), `ServiceOffer`, `MenuItem`, `SchedulingRequirements`, `PaymentCollectionRequirements`, `TeamMember`, `ChannelStatus`, `DeferredCommitment`, `ConversationAnalysis`, `InterviewTurn` |
| `requirements_bot.py` | The engine: `MaterialsAnalyzer` (reads uploads/ into Claude vision+text blocks, distills to inquiry categories / knowledge gaps / notable incidents / customers-owed-replies) and `RequirementsBot` (turn loop, prompt caching, completion gates, heavy code-side post-processing of the model's form output) |
| `interviewer_prompt.txt` | The interviewer's system prompt (~23.5k chars), iteratively improved by automated persona runs |
| `persona_test.py` | Manual persona testing CLI — sessions persisted to disk so an agent can drive one message per invocation |
| `parallel_personas.py` | Mass self-testing harness: generates N fake owner personas (each with a fake WhatsApp export), role-plays them against the bot in parallel, a judge model scores each transcript, then an improver model rewrites `interviewer_prompt.txt` based on aggregated findings. Cost/token accounting per stage. Results in `parallel_runs/<stamp>/` |
| `dashboard.py` | Localhost:8500 live dashboard — 5-stage pipeline console (Persona Generator → Interviewing → Judge → Improver → Fixing Code), parses `run.log` live, per-persona lanes, cost stat boxes, model chips, live-logs page |

## Key design decisions (learned from persona runs)

- The model re-emits the FULL form as JSON every turn (`InterviewTurn`); code-side `_postprocess()` fixes what the model can't be trusted with: dedup across phrasings, provenance tags on upload-derived facts (`[source: uploaded_materials, verified: false]`), auto-generated gap follow-ups (missing turnarounds, conditional prices without amounts, budget with no number), and rebuilding `open_items` as a reference index instead of duplicated text.
- **Completion gates**: when the model flags the interview complete, code refuses if budget/timeline/scope were never asked (one forced rapid-fire sweep) or if the owner made unfulfilled mid-interview promises (one nudge). Each gate fires exactly once to avoid goodbye loops.
- Explicit "unknown" sentinels (`unknown-varies`, `unknown - pending confirmation`) so null always means UNASKED, never "none".
- Materials gate: the bot refuses to interview if uploads exist but couldn't be analyzed — failing loudly beats shipping a brief that looks complete.
- Prompt caching breakpoints (system prompt + conversation prefix) so each turn only pays for new tokens; `max_tokens=16000` because the growing form must never truncate.
- The self-improvement loop: persona runs average-score the prompt, and the prompt file has grown through commits like "parallel personas x5: avg 6.76/10, prompt updated".

## Full source code follows

---

### `main.py`

```python
# main.py — interactive chat with RequirementsBot.
#
# Run:  python main.py
# Type your answers; put chat exports / screenshots into uploads/ when asked
# and tell the bot when they're ready. When the interview completes, the full
# brief is saved to requirements_brief.json.
import json
import sys

from requirements_bot import RequirementsBot

if __name__ == "__main__":
    # Windows consoles often default to cp1252; the model freely uses emoji
    # and accented text, and one print() must never crash the interview.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    bot = RequirementsBot()
    bot.uploads_dir.mkdir(exist_ok=True)  # so the folder exists when the bot mentions it
    print("Bot:", RequirementsBot.GREETING)

    turn = None  # latest turn, so an early exit can still save a partial brief
    while True:
        try:
            user_msg = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user_msg:
            continue
        if user_msg.lower() in ("quit", "exit"):
            break

        try:
            messages, turn = bot.send(user_msg)
        except RuntimeError as e:
            # The materials gate tripped: the bot refuses to interview blind
            # over files it could not read. Stop instead of shipping a brief
            # that looks complete; whatever was gathered is still saved below.
            print(f"\n[Stopped — {e}]")
            print("[Fix or remove the files in uploads/ and run again.]")
            break
        for msg in messages:
            print("\nBot:", msg)

        if bot.complete:
            with open("requirements_brief.json", "w", encoding="utf-8") as f:
                json.dump(bot.brief(turn), f, indent=2, ensure_ascii=False)
            print("\n[Saved the full brief to requirements_brief.json]")
            break

    # The owner quit before confirming: don't lose what was gathered.
    if not bot.complete and turn is not None:
        brief = bot.brief(turn)
        brief["partial"] = True
        with open("requirements_brief.json", "w", encoding="utf-8") as f:
            json.dump(brief, f, indent=2, ensure_ascii=False)
        print("\n[Interview unfinished — saved a PARTIAL brief to requirements_brief.json]")

    print("\nGoodbye!")
```

---

### `models.py`

```python
# models.py — the data schemas of RequirementsBot.
from typing import Optional, Union

from pydantic import BaseModel

# The explicit "we asked, the answer is genuinely indeterminate" value. Without
# it, "the owner never told us" and "the owner told us it varies every year"
# both serialize as null, and a builder cannot tell them apart. Use it as the
# first token of an entry: "unknown-varies — holidays move every year".
UNKNOWN_VARIES = "unknown-varies"

# The other half of that problem: "we asked, and the owner does not know yet —
# someone must look it up". A null here reads as "none", which is how an
# answered "no idea, check with Ute" became "this business holds no
# certifications". Use it as the first token: "unknown - pending confirmation —
# Ute knows which standards we hold".
UNKNOWN_PENDING = "unknown - pending confirmation"


class ServiceOffer(BaseModel):
    """One service/product line with the numbers a bot needs before it may quote.
    services_and_pricing accepts these OR plain strings, so older briefs (and a
    quick free-text capture) stay valid."""
    name: str                                   # "themed cupcakes", "rush order surcharge"
    price: Optional[str] = None                 # exactly as the owner stated it
    price_basis: Optional[str] = None           # "fixed" | "from" (starting price) |
                                                # "range" (a band: use price_min/price_max) |
                                                # "quote only"
    price_min: Optional[str] = None             # lower bound of a stated band ("45 €/hr").
    price_max: Optional[str] = None             # upper bound ("90 €/hr"). A band forced into
                                                # basis "from" silently drops the ceiling, so a
                                                # bot quotes 45 €/hr for a 90 €/hr job — record
                                                # both ends and what moves the price in notes
    minimum_order_quantity: Optional[str] = None    # "6 units", "none"
    fee_trigger_condition: Optional[str] = None     # what makes this fee apply
                                                    # ("orders under 48h notice")
    lead_time: Optional[str] = None             # the turnaround THIS entry assumes. A
                                                # surcharge without it is meaningless —
                                                # rush pricing needs its rush definition
    line_type: Optional[str] = None             # WHAT KIND OF LINE this is:
                                                # "deliverable" (something a customer
                                                # waits for) | "appointment" (an
                                                # in-person slot: the duration IS the
                                                # turnaround) | "discount" | "inclusion"
                                                # (bundled at no extra charge) |
                                                # "add-on" | "surcharge" | "fee".
                                                # RequirementsBot gates its automatic
                                                # "what is the turnaround?" and "what is
                                                # the amount?" follow-ups on this: with
                                                # it null, a "Referral discount" line
                                                # generated a lead-time question that
                                                # swamped the genuine gaps
    price_examples: Optional[list[str]] = None  # real quoted prices seen for THIS line
                                                # ("knotless braids, medium, shoulder
                                                # length — $180"), one per entry. They
                                                # illustrate the range; they are not
                                                # services of their own. Promoting one
                                                # into its own services_and_pricing row
                                                # duplicated the parent line and spawned
                                                # follow-ups against a price example
    notes: Optional[str] = None


class MenuItem(BaseModel):
    """One line of an everyday price list — a dish, a drink, a stocked product.
    Kept apart from services_and_pricing because a food or retail business's core
    catalog was previously scattered: a whole menu survived as the single chat-log
    fragment "2 tacos — $5", which is one transaction, not a price list.
    menu_items accepts these OR plain strings, so a quick free-text capture (and
    any older brief) stays valid."""
    name: str                                   # "taco de asada", "horchata (large)"
    price: Optional[str] = None                 # exactly as the owner stated it
    unit: Optional[str] = None                  # what the price buys ("each", "per 3",
                                                # "per kg") — "2 tacos $5" is a unit, not a price
    variants: Optional[list[str]] = None        # sizes/fillings/options that change the
                                                # price or the order ("large +$1", "with cheese")
    dietary_flags: Optional[list[str]] = None   # "vegetarian", "contains nuts", "halal" —
                                                # a top-frequency customer question the bot
                                                # must not guess at
    availability: Optional[str] = None          # when it is actually on sale ("weekends only",
                                                # "until sold out")
    notes: Optional[str] = None


class NotificationSettings(BaseModel):
    """How a reminder/notification bot should actually behave. For reminder-type
    bots this IS the product, so it gets its own group instead of prose in notes.
    Every field Optional — an owner may only have opinions about some of them."""
    reminder_lead_time: Optional[str] = None   # "24h before, plus 1h before"
    repeat_cadence: Optional[str] = None       # "once, then again if no reply"
    confirm_keywords: Optional[list[str]] = None   # words that confirm ("YES", "OK")
    cancel_keywords: Optional[list[str]] = None    # words that cancel ("CANCEL", "STOP")
    opt_out_handling: Optional[str] = None     # how customers stop receiving messages
    quiet_hours: Optional[str] = None          # times the bot must not message
    reminder_channel: Optional[str] = None     # HOW the reminder is delivered — WhatsApp,
                                               # SMS, email, a push to a task list. Without
                                               # it an agreed reminder feature has no
                                               # transport and ends up parked as a pending
                                               # design decision instead of a spec
    reminder_recipients: Optional[list[str]] = None  # WHO receives it, one per entry:
                                               # "<owner | staff member | the customer> —
                                               # <their number/address> — <which reminders>"
    notes: Optional[list[str]] = None          # anything else notification-specific


class PaymentCollectionRequirements(BaseModel):
    """The mechanics behind any feature that MOVES MONEY (deposit, prepayment,
    booking fee, invoice link). A scope line saying "takes deposits" is not
    buildable: someone has to know which rail the money lands on, through which
    provider, and who checks that it arrived. Every field Optional so a partial
    answer is still recorded, but a money feature with all of them null is a
    gap, not a spec."""
    rail: Optional[str] = None           # the instrument itself: card, cash, bank transfer,
                                         # wallet app (Venmo/Bizum/PIX), invoice on terms
    provider: Optional[str] = None       # who processes it: Stripe, SumUp, the bank, "none —
                                         # the customer just sends it to a handle"
    who_reconciles: Optional[str] = None # the human who confirms the money arrived and marks
                                         # the booking paid, and how often they check
    trigger: Optional[str] = None        # what makes the charge happen (at booking, 24h before,
                                         # on collection) and the amount/percentage
    deposit_amount: Optional[str] = None # the figure actually taken, as stated ("50", "30%")
    deposit_threshold_amount: Optional[str] = None  # the order value AT OR ABOVE which the
                                         # deposit applies, digits only ("200"). trigger is
                                         # free text, so "we ask for one on the bigger jobs"
                                         # could never become a rule anyone can implement
    deposit_threshold_currency: Optional[str] = None  # "USD", "EUR", "BHD"
    exempt_segments: Optional[list[str]] = None  # who is NEVER charged it ("regulars",
                                         # "the two standing restaurant accounts") — the
                                         # exception is half the rule
    refund_handling: Optional[str] = None  # what happens to the money on a cancellation
    notes: Optional[list[str]] = None    # anything else money-movement specific


class TeamMember(BaseModel):
    """One person who works in the business, with the parts a build actually
    uses: what they are to the owner, what they can and cannot take on, when
    they are there, and whether the bot may route work to them.

    Staff used to be prose inside business_description ("Beto does the
    transmissions, Tavo is learning"), which is exactly how a transmission
    specialist and an apprentice arrived downstream as two interchangeable
    names. team_members accepts these OR plain strings for back-compat."""
    name: str
    role: Optional[str] = None                    # "transmission mechanic", "apprentice"
    relationship: Optional[str] = None            # to the owner/business: "owner's son",
                                                  # "hired 2023", "sister, helps weekends"
    skills_or_limitations: Optional[str] = None   # what only they can do, and what they
                                                  # may NOT be given ("learning — never
                                                  # books gearbox work on his own")
    services_performed: Optional[list[str]] = None  # which catalog lines they can perform
    days: Optional[str] = None                    # "Tue-Sat"
    hours: Optional[str] = None                   # "10:00-18:00"
    availability_notes: Optional[str] = None      # "in school, weekends only"
    can_book: Optional[bool] = None               # may the bot place a booking on them
    can_answer_customer_questions: Optional[bool] = None  # may the bot hand a customer to
                                                  # them, or are they hands-off
    notes: Optional[str] = None


class SchedulingRequirements(BaseModel):
    """The mechanics behind any feature that PLACES A BOOKING. "The bot books
    appointments" in solution_scope is a promise, not a spec: someone has to
    know what is bookable, how long a slot is, how many can run at once, who
    confirms it and what happens when it is cancelled. Every field Optional so
    a partial answer still lands, but a scheduling scope with all of them null
    is a gap, not a feature."""
    bookable_services: Optional[list[str]] = None  # which catalog lines the bot may book
                                                   # (rarely all of them)
    slot_length: Optional[str] = None      # the calendar granularity ("30 min", "per
                                           # service duration")
    concurrency: Optional[str] = None      # how many customers can be served AT ONCE, and
                                           # what sets that number
    resources: Optional[list[str]] = None  # the physical things a booking consumes:
                                           # chairs, bays, rooms, tables — one per entry
    who_confirms: Optional[str] = None     # who turns a request into a confirmed booking:
                                           # the bot itself, the owner, whoever is on shift
    booking_lead_time: Optional[str] = None  # shortest notice accepted, how far ahead the
                                           # calendar opens
    walk_in_handling: Optional[str] = None  # what happens to people who just turn up
    cancellation_policy: Optional[str] = None  # notice required, fee, who may waive it
    no_show_policy: Optional[str] = None   # what the business does, what the bot says
    notes: Optional[list[str]] = None


class ChannelStatus(BaseModel):
    """One channel the owner named, and its real state.

    Two different holes, one shape. "Website, maybe" went into channels as if a
    site existed, so a build team read a deployment target where there was
    nothing to deploy to; and a channel raised but never confirmed (a Facebook
    page nobody came back to) fell into additional_notes and out of scope
    silently. Both fields below take these OR plain strings."""
    channel: str                           # "WhatsApp", "Instagram DMs", "the website"
    in_scope: Optional[bool] = None        # None means RAISED BUT NEVER CONFIRMED either
                                           # way — the state that used to vanish
    readiness: Optional[str] = None        # "exists" | "planned" | "does not exist yet —
                                           # must be built first"
    platform: Optional[str] = None         # what it runs on/where it is hosted (Wix,
                                           # Shopify, WhatsApp Business API)
    owned_by: Optional[str] = None         # who holds the login, the domain, the page
    notes: Optional[str] = None            # their words on it, why it is or is not in


class DeferredCommitment(BaseModel):
    """Something the OWNER promised to supply during the interview and hadn't
    yet — "ahorita te digo", "tengo que checar el papelito", "I'll text you the
    address tonight".

    These only ever surfaced after the fact, as a line in
    unresolved_business_facts, with nothing going back for them while the owner
    was still on the call. Recorded here the moment it is said, RequirementsBot
    forces one re-ask before it will accept the interview as complete."""
    item: str                               # what is owed ("the exact street address")
    promised_as: Optional[str] = None       # their words, so the nudge can quote them
    how_delivered: Optional[str] = None     # how it will arrive ("he'll WhatsApp a photo
                                            # of the paper"), and by when
    asked_again: Optional[bool] = None      # set true once it has been chased in-interview
    received: Optional[bool] = None         # true once they actually supplied it — an
                                            # entry that never goes true ships as an owner
                                            # follow-up


class BusinessRequirements(BaseModel):
    """The intake form. Every field Optional because it starts empty and fills up."""
    contact_name: Optional[str] = None           # the person we're interviewing
    business_name: Optional[str] = None
    industry: Optional[str] = None
    business_description: Optional[str] = None
    years_in_business: Optional[str] = None      # how long they have traded, as they said it
                                                 # ("9 years", "since 2016") — the bot is
                                                 # asked this constantly and it belongs in
                                                 # the About copy, so it is not background
    founding_story: Optional[str] = None         # how the business started, in one or two
                                                 # sentences ("founded 9 years ago after
                                                 # leaving a corporate job"). Copy material
                                                 # for the bot's own answers; keep the
                                                 # human detail that drives no build
                                                 # decision in background_color
    locations: Optional[list[str]] = None        # WHERE the business physically is and operates,
                                                 # one per entry: "<primary | alternate/rain
                                                 # /contingency | service radius> — <full street
                                                 # address INCLUDING city and region> — <when it
                                                 # applies>". A brief with no city cannot be
                                                 # built against, and a fallback pitch ("under
                                                 # the bridge on Main when it rains") is a place
                                                 # the bot must be able to name, not prose
    problem_to_solve: Optional[str] = None       # WHY they want a chatbot
    target_audience: Optional[str] = None        # who will talk to it
    channels: Optional[list[str]] = None         # where the BOT should live (WhatsApp, Instagram...);
                                                 # other ways customers reach the business today
                                                 # belong in business_description
    channel_readiness: Optional[list[Union[ChannelStatus, str]]] = None
                                                 # DOES EACH CHANNEL IN channels ACTUALLY EXIST
                                                 # YET — one ChannelStatus per channel with its
                                                 # readiness, platform and who owns the login.
                                                 # "Website, maybe" collapsed into channels as a
                                                 # deployment target, and nothing recorded that
                                                 # the site has to be BUILT before anything can
                                                 # ship on it
    channels_considered_not_in_scope: Optional[list[Union[ChannelStatus, str]]] = None
                                                 # channels the owner RAISED but never confirmed
                                                 # in or out — a Facebook page mentioned once and
                                                 # dropped. Leave in_scope null when it was never
                                                 # settled: that is the whole point of the field,
                                                 # and as free text in additional_notes it was
                                                 # indistinguishable from a decision
    adjacent_asks: Optional[list[str]] = None    # things BEYOND the chatbot that the owner asked
                                                 # for out loud — a website, online ordering, a
                                                 # POS or card reader, an app. Not in scope for
                                                 # the bot, but a stated want with nowhere to
                                                 # land was simply lost. One per entry:
                                                 # "<what they want> — <their words/why> — <how
                                                 # it relates to the bot>"
    integrations: Optional[list[str]] = None     # booking system, CRM, order DB...
    conversation_volume: Optional[str] = None    # rough conversations/day, hours coverage
    languages: Optional[list[str]] = None
    success_criteria: Optional[str] = None       # what "working" means to them
    solution_scope: Optional[str] = None         # simple Q&A / lead capture vs. full booking+payment app
    scheduling_requirements: Optional[SchedulingRequirements] = None
                                                 # the BOOKING mechanics behind solution_scope.
                                                 # Required the moment the scope includes
                                                 # appointments, slots or a calendar: what is
                                                 # bookable, slot length, how many at once, who
                                                 # confirms, cancellation and no-show. Scheduling
                                                 # was being accepted into scope with nowhere to
                                                 # record any of it, so the brief asserted a
                                                 # feature with zero specification
    lead_capture_fields: Optional[list[str]] = None  # WHAT THE BOT MUST COLLECT from an enquirer
                                                 # before a human can answer, one per entry:
                                                 # "<what to collect> — <why it is needed / what
                                                 # it changes> — <required | optional>" ("event
                                                 # date — decides availability and season pricing
                                                 # — required"). Derive it from how the owner
                                                 # themselves works out a quote. It is the core
                                                 # of any quoting or enquiry bot and had no home
    payment_collection_requirements: Optional[PaymentCollectionRequirements] = None
                                                 # the money mechanics BEHIND solution_scope.
                                                 # Required the moment the scope includes a
                                                 # deposit, booking fee, prepayment or any other
                                                 # feature that takes money: rail, provider and
                                                 # who reconciles it. Distinct from
                                                 # payment_methods, which is what the BUSINESS
                                                 # accepts today whether or not the bot touches it
    constraints: Optional[list[str]] = None      # compliance, privacy (GDPR), approvals, tech limits
    budget: Optional[str] = None                 # their words, verbatim ("small money if
                                                 # simple"). Kept as the free-text record, but
                                                 # it is NOT a scopeable number on its own —
                                                 # the four fields below carry the figure
    budget_amount_min: Optional[str] = None      # the numeric floor the owner gave, with no
                                                 # currency symbol ("500"). A vague answer gets
                                                 # ONE follow-up for a figure they would not
                                                 # want to go past; a brief with free text and
                                                 # no number here is flagged as a gap
    budget_amount_max: Optional[str] = None      # the numeric ceiling ("1500"). Fill both to
                                                 # the same value when they named one figure
    budget_currency: Optional[str] = None        # "USD", "EUR", "BHD" — a bare 1500 is
                                                 # unscopeable
    budget_confidence: Optional[str] = None      # how firm the figure is: "firm" (a decided
                                                 # budget) | "estimate" (a guess they gave when
                                                 # pushed) | "refused" (asked, would not say) |
                                                 # "scope-dependent — <what it depends on>"
    timeline: Optional[str] = None
    # --- the chatbot's actual knowledge, gathered during the interview ---
    services_and_pricing: Optional[list[Union[ServiceOffer, str]]] = None
                                                      # every service, price, discount, surcharge,
                                                      # subscription. Prefer a ServiceOffer object per line
                                                      # so a minimum order, a fee's trigger, "from" vs. fixed
                                                      # pricing and the lead time it assumes are separable;
                                                      # plain strings still validate for back-compat
    menu_items: Optional[list[Union[MenuItem, str]]] = None
                                                      # THE EVERYDAY PRICE LIST: dishes, drinks, stocked
                                                      # products — one MenuItem per line with its price,
                                                      # unit, variants and dietary flags. Separate from
                                                      # services_and_pricing (which holds SERVICE lines,
                                                      # tiers and surcharges) because a food or retail
                                                      # business's core catalog kept ending up as stray
                                                      # chat-log fragments like "2 tacos — $5". A shop
                                                      # whose menu is null was never walked through its
                                                      # own price list
    service_durations: Optional[list[str]] = None     # how long each service takes ("alignment: 45 min"),
                                                      # needed to place bookings on a calendar. Use
                                                      # "unknown-varies — <reason>" when the owner answered
                                                      # but the duration genuinely varies; null means UNASKED
    operating_hours: Optional[list[str]] = None       # the normal weekly pattern, one entry per day or
                                                      # day-range: "Tue-Sun — 11:00-21:00",
                                                      # "Mon — closed". This is the calendar the bot
                                                      # answers "are you open?" from, so it cannot live
                                                      # inside faq_answers prose. Use
                                                      # "unknown-varies — <reason>" when the pattern
                                                      # genuinely shifts; null means UNASKED.
                                                      # Exceptions to this pattern go in holiday_closures
    holiday_closures: Optional[list[str]] = None      # dates/rules the business is shut outside its normal
                                                      # weekly hours ("closed on holiday Mondays", "Aug 1-15").
                                                      # "unknown-varies — <reason>" when the owner answered
                                                      # "it changes every year"; null means UNASKED
    notification_settings: Optional[NotificationSettings] = None  # reminder/notification behaviour
    partners_and_referrals: Optional[list[str]] = None  # work sent OUT to others and partners relied upon
                                                      # ("transmissions go to the Alameda shop", "towing
                                                      # via Joe's") — the bot must route these, not quote them
    faq_answers: Optional[list[str]] = None           # confirmed question->answer pairs the bot can use
    payment_methods: Optional[list[str]] = None       # the CANONICAL home for what the business accepts
                                                      # today, one rail per entry with its handle and any
                                                      # limit: "Venmo — @tacoslacamachito",
                                                      # "cash — preferred", "card — not accepted, no
                                                      # reader". Previously duplicated across faq_answers
                                                      # and business_policies, so a builder had two
                                                      # half-lists and no source of truth. The policy
                                                      # AROUND the money (deposits, refunds) stays in
                                                      # business_policies
    business_policies: Optional[list[str]] = None     # cancellations, rush requests, coverage area, VAT...
    remote_service_policy: Optional[list[str]] = None  # whether work can arrive WITHOUT the customer
                                                      # coming in — mail-in, drop-off by courier, remote
                                                      # or online delivery — with DOMESTIC and
                                                      # INTERNATIONAL answered separately, one entry each:
                                                      # "<domestic | international> — <accepted? / refused?>
                                                      # — <who pays shipping, turnaround, customs/duties,
                                                      # insurance, what the bot should tell them>".
                                                      # The uploads usually show only the one case that
                                                      # happened to come up, so the other half was never
                                                      # asked and the bot answered it wrong. "not offered"
                                                      # is a valid, required answer; null means UNASKED
    escalation_rules: Optional[list[str]] = None      # what goes to a human, to whom, via what channel.
                                                      # "none exists — bot should set expectations" is a
                                                      # VALID value: the absence of a rule is a requirement
    escalation_contacts: Optional[list[str]] = None   # STRUCTURED CONTACTS ONLY, one per entry:
                                                      # "<name/role> — <number/channel> — <when>", or the
                                                      # single entry "none" when there is no backup person.
                                                      # Narrative about what should happen belongs in
                                                      # escalation_rules, never here
    capacity_constraints: Optional[list[str]] = None  # the owner's operational rules of thumb and limits,
                                                      # in their own numbers ("2-3 events a weekend with
                                                      # my sister helping", "max 8 covers past 9pm")
    team_members: Optional[list[Union[TeamMember, str]]] = None
                                                      # EVERY PERSON who works in the business, one
                                                      # TeamMember each — name, role, relationship,
                                                      # what they can and cannot do, days/hours, and
                                                      # whether the bot may book them or hand a
                                                      # customer to them. The single home for staff:
                                                      # they were previously split across
                                                      # business_description prose, capacity_constraints,
                                                      # escalation_contacts and additional_notes, which
                                                      # is how a named specialist and an apprentice
                                                      # arrived downstream as two bare first names.
                                                      # escalation_contacts still holds the CONTACT
                                                      # ROUTE for whoever covers out of hours
    concurrent_capacity: Optional[str] = None         # how many jobs/customers can run AT ONCE across
                                                      # the whole business, and what sets the ceiling
                                                      # ("2 — one chair each, Ana and Rosa"). The
                                                      # single number a scheduler needs; as a sentence
                                                      # inside capacity_constraints it was unusable
    venue_capacity: Optional[list[str]] = None        # PHYSICAL ROOM for hospitality: seats, covers,
                                                      # table sizes, standing capacity — one per entry
                                                      # ("28 seats inside", "4 tables of 6, 2 of 2").
                                                      # Its own field because capacity_constraints is
                                                      # the operational rules ("we run out of birria
                                                      # by 2pm"), and a bot that must answer "can you
                                                      # fit 8 of us?" needs the number, not the rule
    current_systems_and_records: Optional[list[str]] = None  # where the data the bot would need actually
                                                      # lives TODAY, one system per entry: "<what —
                                                      # paper forms / Excel on a local server / an ERP /
                                                      # one person's phone> — <what it holds> — <who
                                                      # maintains it> — <backed up? reachable from
                                                      # outside?>". Without a field for it the
                                                      # interviewer never asked, and a brief that does
                                                      # not say the quotes live in a paper binder is
                                                      # missing the hardest part of the build
    equipment_and_capacity_assets: Optional[list[str]] = None  # the physical things that set the ceiling,
                                                      # one per entry: "<count> x <make/model> <what it
                                                      # is> — <age/condition> — <what it limits>"
                                                      # ("5 x Deckel milling machines — oldest from
                                                      # 1994"). The count and the make are lookup data;
                                                      # buried in a capacity_constraints sentence they
                                                      # are unusable. The RULES those assets imply stay
                                                      # in capacity_constraints
    capex_and_equipment_decisions: Optional[list[str]] = None  # PURCHASES THE OWNER IS WEIGHING but has not
                                                      # made — a second generator, a card reader, a van, a
                                                      # new oven — one per entry: "<the thing> — <cost as
                                                      # they stated it> — <decided | undecided | ruled out>
                                                      # — <what it depends on / what it unblocks for the
                                                      # bot>". Distinct from the other two homes it used to
                                                      # be split between: equipment_and_capacity_assets is
                                                      # what they ALREADY own, adjacent_asks is work they
                                                      # want done BEYOND the bot. An investment question the
                                                      # owner flagged out loud ("is a card reader even worth
                                                      # it?") is neither, and was being scattered across
                                                      # both or lost entirely
    customer_segments: Optional[list[str]] = None     # the distinct kinds of customer/order the bot must tell
                                                      # apart, each with its OWN intake rules, one per entry:
                                                      # "<retail walk-in | event/custom order | wholesale or
                                                      # standing commercial account> — <who> — <how the order
                                                      # arrives, minimums, lead time, invoicing/payment>".
                                                      # A restaurant with a weekly standing order does not
                                                      # book like a walk-in and must not share its flow
    pricing_uncertainties: Optional[list[str]] = None  # prices the owner CANNOT state yet and why — rising
                                                      # input costs, unrepriced lines, margins they suspect
                                                      # are wrong: "<what> — <why> — <may the bot quote it?>".
                                                      # This decides whether the bot quotes at all, so it is
                                                      # not the same thing as a missing number in
                                                      # unresolved_business_facts
    open_strategic_decisions: Optional[list[str]] = None  # BUSINESS decisions the owner has not made,
                                                      # one per entry: "<the decision> — <the options as
                                                      # they framed them> — <what it hinges on / when
                                                      # they decide> — <what it would change for the
                                                      # bot>" ("whether to open a second office in
                                                      # Berlin — undecided, depends on Q3 — a second
                                                      # location means a second set of hours and a
                                                      # routing question"). Distinct from
                                                      # pricing_uncertainties (a price they cannot
                                                      # state) and from out_of_scope_asides (decisions
                                                      # with no build implication at all): a live
                                                      # strategic question that WOULD change the build
                                                      # had nowhere to live, so it was either lost or
                                                      # mis-filed as a templated missing fact
    certifications_and_standards: Optional[list[str]] = None  # quality/regulatory standards, one per entry:
                                                      # "<standard> — held since <when> / under consideration
                                                      # — <cost, timeline, who audits, why>". Held and
                                                      # aspirational both belong here, never in faq_answers.
                                                      # "I don't know, ask Ute" is an ANSWER: write
                                                      # "unknown - pending confirmation — <who knows>"
                                                      # rather than null, which downstream reads as "this
                                                      # business holds no standards"
    upcoming_business_changes: Optional[list[str]] = None  # known-but-unsettled changes to the business that
                                                      # the build must survive: lease renewal, a second
                                                      # location, new staff, a certification in progress —
                                                      # "<change> — <timing> — <what it depends on>"
    marketing_channels_and_ad_budget: Optional[list[str]] = None  # how customers are acquired today
                                                      # (word of mouth, Instagram, a sign), what they spend or
                                                      # would spend on ads, and any marketing question they
                                                      # raised ("should I pay for Facebook ads?")
    stakeholders: Optional[list[str]] = None          # everyone with a say in this decision besides the
                                                      # contact, one per entry: "<name> — <role/relationship>
                                                      # — <influence on the decision> — <stance>". A spouse
                                                      # or partner who disagrees is a project risk, not
                                                      # business background
    adoption_risks: Optional[list[str]] = None        # feelings that a BUILDER must act on: discomfort with
                                                      # automation, digital illiteracy, fear of losing the
                                                      # human touch — each with the reason the owner gave
    background_color: Optional[list[str]] = None      # human context that is NOT a requirement (the crying
                                                      # customer, the father who still visits) — kept so the
                                                      # brief reads true, but it drives no build decision
    owner_sentiment_or_concerns: Optional[list[str]] = None  # LEGACY POINTER LIST to adoption_risks ONLY,
                                                      # auto-filled by RequirementsBot so older consumers
                                                      # keep working. It is NO LONGER a verbatim copy:
                                                      # every entry reads "[see adoption_risks #n] <short
                                                      # excerpt>", because mirroring the full text put six
                                                      # identical sentences in two fields and a reader
                                                      # could not tell which was the source.
                                                      # background_color is not pointed at either —
                                                      # "founded in 1978 by his father" is not a
                                                      # sentiment. Write adoption_risks or
                                                      # background_color instead of this one
    maintenance_and_ownership: Optional[str] = None   # who updates the bot's content after launch, how
                                                      # often, appetite for paid managed updates, and what
                                                      # training the owner needs
    # --- provenance: which side of the interview a fact came from ---
    facts_from_uploads: Optional[list[str]] = None    # facts taken from the UPLOADED MATERIALS (quoted
                                                      # turnaround times, informal discounts, unanswered
                                                      # threads). Auto-seeded from the materials analysis so
                                                      # they can be cross-checked against the transcript
    upload_provenance_status: Optional[str] = None    # WHETHER THE UPLOADED MATERIALS CAN BE TRUSTED
                                                      # AT ALL, as a single top-level statement:
                                                      # "accepted as theirs" | "owner disputes the
                                                      # provenance/authorization of the uploaded chat
                                                      # logs — <their words>" | "not discussed". An
                                                      # owner who challenges where the files came from
                                                      # invalidates every fact derived from them at
                                                      # once, and that verdict was being crammed into
                                                      # additional_notes where no consumer looks
    fact_conflicts: Optional[list[str]] = None        # contradictions between sources, never silently
                                                      # merged: "hours — materials: 'most weekdays' vs.
                                                      # owner: 'evenings only' — unresolved"
    # --- follow-ups, split by WHO must act (open_items is an auto-built index) ---
    deferred_owner_commitments: Optional[list[Union[DeferredCommitment, str]]] = None
                                                            # things the owner promised MID-INTERVIEW
                                                            # and has not handed over yet. Recorded the
                                                            # turn they say it, so the promise can be
                                                            # chased while they are still here:
                                                            # RequirementsBot forces one re-ask before
                                                            # it accepts the interview as complete, and
                                                            # anything still outstanding at the end is
                                                            # copied into unresolved_business_facts.
                                                            # Set received true once it arrives
    unresolved_business_facts: Optional[list[str]] = None   # facts the OWNER must still supply (IBAN,
                                                            # kids-menu prices, exact opening hours)
    pending_design_decisions: Optional[list[str]] = None    # bot/product decisions still open (how to
                                                            # handle a thumbs-up reply, tone, fallback)
    customer_replies_owed: Optional[list[str]] = None       # replies the owner owes real customers,
                                                            # surfaced from the materials. Every entry
                                                            # ENDS with its provenance, because an item
                                                            # the owner rejected was becoming an action
                                                            # item anyway: "<who/when> — <what they
                                                            # asked> (quote: '...') — status: confirmed
                                                            # | disputed_by_owner | unverified".
                                                            # "confirmed" only once the owner agrees
                                                            # this person is really waiting; an entry
                                                            # contradicted in fact_conflicts is
                                                            # "disputed_by_owner" and must say so here
                                                            # too, never silently stay on the list
    open_items: Optional[list[str]] = None            # NOT a bucket to choose: RequirementsBot rebuilds this
                                                      # every turn as a REFERENCE INDEX into the three lists
                                                      # above — "[owner fact #2] <short excerpt>" — never a
                                                      # second copy of their text, which is what made the
                                                      # same item appear three times in one brief. Follow
                                                      # the reference for the full wording. Strictly things
                                                      # the DELIVERY TEAM must action or chase — never an
                                                      # internal debate the business has with itself, and
                                                      # never an uncertainty nobody voiced
    out_of_scope_asides: Optional[list[str]] = None   # real things the owner said that the delivery team must
                                                      # NOT action: internal family disagreements, business
                                                      # decisions of their own (whether to open Sundays),
                                                      # musings unrelated to the bot. Kept so nothing is lost,
                                                      # parked so open_items stays a work list
    additional_notes: Optional[list[str]] = None      # important facts that fit no other field — never lose a fact


class ConversationAnalysis(BaseModel):
    """What the analysis of uploaded materials produces."""
    inquiry_categories: list[str]    # e.g. "Appointment booking (~40%): customers ask for slots"
    resolved_patterns: list[str]     # inquiries where the materials SHOW how staff answers
    knowledge_gaps: list[str]        # inquiries whose resolution is NOT visible -> ask the owner
    facts_learned: list[str]         # hard facts about the business found in the materials
    # Concrete incidents, each tied to its date/time and a short quote, so the
    # interviewer can probe the actual pain point live ("I saw the
    # international customer waited 3 weeks — is that typical?").
    # Defaulted: older saved analyses have no such key.
    notable_incidents: list[str] = []
    # Customers in the materials still waiting on an answer. Flows straight
    # into requirements.customer_replies_owed instead of waiting for the owner
    # to volunteer it. Defaulted for the same back-compat reason.
    open_customer_requests: list[str] = []


class InterviewTurn(BaseModel):
    """What the interviewer model must output EVERY turn."""
    requirements: BusinessRequirements   # the form, updated with everything learned so far
    next_message: str                    # the question (or final summary) to show the user
    interview_complete: bool             # True only when enough is gathered and confirmed
    run_file_analysis: bool = False      # True when the owner just said their files are ready
```

---

### `requirements_bot.py`

```python
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
                "chatbot. What's your name?")
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
    DEFERRED_NUDGE_NOTE = (
        "(System note: you flagged the interview complete, but the owner "
        "promised to supply these during the interview and never did: "
        "{items}. Your closing message was NOT sent to the owner. Go back for "
        "them NOW in ONE short message — quote their own promise back to them "
        "and make one concrete ask for each ('can you send me the photo of "
        "that paper now?'), not a passive 'whenever you get a chance'. Do not "
        "re-summarize. Whatever they answer, record it — set received true on "
        "anything they supply — and close out on your next turn: this is the "
        "only time you will be sent back for these.)")

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
        # The same, for promises the owner made and never kept ("ahorita te
        # digo"). Also exactly one, for the same reason.
        self.deferred_nudged: bool = False
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

        # DEFERRED-PROMISE GATE. "I'll check the paper and tell you in a
        # second" is the one follow-up that is free to close WHILE THE OWNER IS
        # STILL HERE, and it was the one that never got closed: the promise
        # surfaced only afterwards, as a line in unresolved_business_facts, and
        # became the delivery team's problem. One nudge, then the interview may
        # end whether or not they came through.
        outstanding = _outstanding_commitments(turn.requirements)
        if turn.interview_complete and outstanding and not self.deferred_nudged:
            self.deferred_nudged = True
            turn.interview_complete = False
            messages.pop()
            turn = self._ask(
                self.DEFERRED_NUDGE_NOTE.format(items="; ".join(outstanding)),
                record_as="(deferred-promise nudge)")
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
            "deferred_nudged": self.deferred_nudged,
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
        bot.deferred_nudged = state.get("deferred_nudged", False)  # likewise
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

        # Material-derived facts get their own home instead of being
        # indistinguishable from what the owner said out loud — and a
        # provenance tag, because the analyzer does occasionally assert
        # something the material never said (it once "learned" the business's
        # channel from the export format). Tagged facts are leads to confirm,
        # not established facts: the prompt forbids promoting one into a normal
        # field before the owner verifies it.
        # Unconditional, even with no analysis in hand: the merge is also what
        # collapses the model's OWN duplicates, and gating it on self.analysis
        # meant a resumed session whose analysis did not survive the round trip
        # shipped every upload fact twice, tagged both ways at once.
        if self.analysis or requirements.facts_from_uploads:
            requirements.facts_from_uploads = _merge_upload_facts(
                requirements.facts_from_uploads,
                self.analysis.facts_learned if self.analysis else [])

        if self.analysis:
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
        awaiting_turnaround: list[str] = []   # collapsed into one entry below
        for offer in requirements.services_and_pricing or []:
            if isinstance(offer, str):
                continue
            trigger = offer.fee_trigger_condition or ""
            applies = f" (applies when: {trigger})" if trigger else ""
            # A discount, an inclusion or a bundled add-on is not something a
            # customer waits for or pays a separate amount for, so neither
            # generator applies to it. Ungated, they invented "turnaround/lead
            # time for 'Referral discount'" and "price variants for 'Local
            # shoots — no extra charge'", and three lines of nonsense buried
            # the one real gap in the same list.
            deliverable = _is_deliverable(offer)
            gaps = []
            if not offer.lead_time and deliverable:
                if any(w in f"{offer.name} {trigger}".lower() for w in _RUSH_WORDS):
                    gaps.append(
                        f"lead time/turnaround for '{offer.name}'{applies} — a rush "
                        f"price was recorded without the turnaround it buys, so the "
                        f"bot cannot say what the extra money gets the customer")
                elif (complete
                      and _line_type(offer) not in _APPOINTMENT_TYPES
                      and not _has_duration(offer, requirements.service_durations)):
                    # Every persona run shipped a catalog with no turnaround on
                    # any line, and nothing said so. For repair and
                    # made-to-order work "how long will it take?" is the most
                    # common question the bot will receive, so a null here is
                    # named as unasked rather than shipped as silence.
                    # Two exemptions, both cases where the answer IS on record:
                    # an in-person appointment, whose duration is its
                    # turnaround, and any line service_durations already
                    # covers — "turnaround for 'Kids braiding' never recorded"
                    # shipped against a brief that recorded 1-1.5 hours for it.
                    awaiting_turnaround.append(offer.name)
            if trigger and deliverable and _price_amounts_missing(offer):
                gaps.append(
                    f"price variants for '{offer.name}'{applies} — the price is "
                    f"conditional but the amount for each case was never stated")
            if gaps:
                requirements.unresolved_business_facts = merge(
                    requirements.unresolved_business_facts, gaps)

        # ONE follow-up for missing turnarounds, however many lines are missing
        # one. The per-line template and the catalog-wide one both fired, so a
        # five-service brief shipped five near-identical lead-time prompts plus
        # a sixth global copy, and the genuinely missing facts sat underneath
        # them. Anything this generator wrote on an earlier turn (and carried
        # back to us in the model's output) is cleared first, so a growing
        # catalog re-words the entry instead of stacking up another one.
        kept = [item for item in (requirements.unresolved_business_facts or [])
                if not item.lower().startswith(_TURNAROUND_PREFIX)]
        requirements.unresolved_business_facts = kept or None
        if awaiting_turnaround:
            lines = ", ".join(f"'{name}'" for name in awaiting_turnaround)
            requirements.unresolved_business_facts = merge(
                requirements.unresolved_business_facts,
                [f"{_TURNAROUND_PREFIX}{lines} — never recorded; ask how long a "
                 f"customer waits for each (and use '{UNKNOWN_VARIES} — <reason>' "
                 f"if it genuinely varies)"])
        # The same gap one level up: not one duration recorded for a business
        # that has services at all.
        elif (complete and requirements.services_and_pricing
                and not requirements.service_durations):
            requirements.unresolved_business_facts = merge(
                requirements.unresolved_business_facts, [DURATIONS_GAP])

        # A promise the owner made and never kept is work for the delivery
        # team, so it joins the owner's follow-up list at the end — after the
        # in-interview nudge in send() has already had its one go at closing it.
        if complete:
            still_owed = [
                f"{item} — the owner said during the interview that they would "
                f"supply this, and it never arrived"
                for item in _outstanding_commitments(requirements)]
            if still_owed:
                requirements.unresolved_business_facts = merge(
                    requirements.unresolved_business_facts, still_owed)

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
        for attempt in range(3):
            messages = self._build_messages(question)
            if attempt:
                # A bare retry replays the identical request and fails the
                # identical way (short/gibberish owner messages reliably tempt
                # the model into replying as plain chat text). Tell it what
                # went wrong so the retry actually differs.
                messages.append(AIMessage(content=output))
                messages.append(HumanMessage(content=(
                    "FORMAT ERROR: that reply was not the required JSON. Resend "
                    "the same content as one valid JSON object matching the "
                    "format instructions, with your chat text in next_message. "
                    "Output nothing except the JSON object.")))
            reply = self.llm.invoke(messages)
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

# ServiceOffer.line_type values for lines nobody waits for and nobody pays a
# separate amount for. The turnaround and price-variant generators skip these:
# "how long does 'Referral discount' take?" is not a gap, it is noise on the
# same list as the real ones.
_NON_DELIVERABLE_TYPES = frozenset({
    "discount", "inclusion", "included", "add-on", "addon", "policy"})
# ...and line_types whose turnaround is already answered by the appointment
# itself. A 90-minute in-person slot has no separate lead time to ask about.
_APPOINTMENT_TYPES = frozenset({"appointment", "in-person appointment", "booking",
                                "slot"})
# Fallback for briefs written before line_type existed, and for a model that
# leaves it null. Deliberately narrow and deliberately excludes "surcharge" and
# "fee": a rush fee genuinely does need the turnaround it buys, which is what
# the _RUSH_WORDS branch asks for.
_NON_DELIVERABLE_WORDS = ("discount", "included", "inclusion", "no extra charge",
                          "no charge", "complimentary", "referral")
# Every turnaround follow-up this module writes starts with it, so the previous
# turn's version can be found and replaced instead of accumulating.
_TURNAROUND_PREFIX = "turnaround/lead time for "

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
    """An entry LABELLED with the field and position that own it.

    The label is what stops the index and the legacy sentiment mirror from
    reading as second copies: a reader can see which field the item really
    lives in. The TEXT ships whole. Clipping it to a fixed width to make that
    point produced stubs like "[see adoption_risks #1] Priya doesn't fully
    trust a bot to touch her calendar directly, worried..." — a sentence that
    stops mid-thought is not a pointer, it is a broken quote, and every
    consumer downstream had to go and reassemble the brief by hand."""
    return f"[{label} #{position}] {' '.join(item.split())}"


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


def _line_type(offer) -> str:
    """The catalog line's declared kind, normalized ("" when unset)."""
    return (offer.line_type or "").strip().lower()


def _is_deliverable(offer) -> bool:
    """Is this line something a customer WAITS for and pays a price for?

    A discount, a bundled inclusion or a no-extra-charge add-on is neither, so
    the automatic turnaround and price-variant follow-ups must not fire on it.
    The declared line_type decides; a name-based fallback catches the obvious
    cases in briefs written before the flag existed."""
    kind = _line_type(offer)
    if kind:
        return kind not in _NON_DELIVERABLE_TYPES
    name = (offer.name or "").lower()
    return not any(word in name for word in _NON_DELIVERABLE_WORDS)


def _has_duration(offer, durations: Optional[list[str]]) -> bool:
    """Is this line's timing already on record in service_durations?

    Durations are written "<line> — <how long>", so the line's name appearing
    in one is the answer. Without this check the interview captured "Kids
    braiding — 1-1.5 hours" from the owner and then shipped "turnaround/lead
    time for 'Kids braiding' — never recorded" in the same brief."""
    name = _norm(offer.name or "")
    return bool(name) and any(name in _norm(entry) for entry in durations or [])


def _outstanding_commitments(requirements: BusinessRequirements) -> list[str]:
    """What the owner promised to supply and has not.

    A bare string entry counts as outstanding: older briefs and a hurried model
    write the promise without the received flag, and the cost of one extra
    question is far below the cost of losing the address."""
    outstanding = []
    for entry in requirements.deferred_owner_commitments or []:
        if isinstance(entry, str):
            item = entry.strip()
        elif entry.received:
            continue
        else:
            item = (entry.item or "").strip()
        if item:
            outstanding.append(item)
    return outstanding


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
    owner is a one-way door.

    Matching is on the bare text and nothing looser. A word-overlap fallback
    for facts the model RE-WORDED rather than re-emitted was tried and removed:
    at any threshold loose enough to catch "an NDA gets signed before every
    scoping call" against "NDA is signed before any scoping call", it also
    merged "prices went up in March" into "prices went up in April". Losing a
    fact is worse than shipping two phrasings of one."""
    facts: dict[str, tuple[bool, str]] = {}
    for item in list(existing or []) + list(learned):
        verified, fact = _split_upload_tag(item)
        if not fact:
            continue
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
```

---

### `interviewer_prompt.txt`

```text
You are RequirementsBot, a friendly consultant conducting a discovery interview with a
business owner who wants a chatbot built for their business.

Your goal: fill in the requirements form through natural conversation.

HOW TO TALK
- WRITING STYLE for the "next_message" text you write (this rule changes ONLY the
  wording of next_message; it never changes your required JSON output format,
  which always applies): never use em-dashes or double hyphens ("—", "--"); use a
  comma, a period, or a new sentence instead. Keep next_message SHORT: at most
  two sentences, then your one question. No preamble like "Let's start with the
  big picture". Use everyday words a non-technical person understands instantly;
  if a term needs explaining, it is the wrong term.
- Your very FIRST question must be the person's name (unless they already gave it).
  Then greet them by name and use it naturally now and then — not every message.
- Exactly ONE question per turn. Never a list, never two questions stitched with
  "and also". Keep questions short, warm, plain-spoken, no jargon.
- Vary acknowledgments and transitions. No stock praise ("love it", "smart move",
  "that's really useful") and no repeated formula ("changing the subject", "quick
  one") — filler burns turns and makes the back half read as a form being walked.
- Listen first: if their message already answers fields, record them and never ask
  again. Check what you already hold before every question. Never ask a near-
  duplicate of an earlier question — fold closely related facts into one question
  instead of spreading them over turns.
- ASK OPEN, NOT LEADING. Never offer two pre-framed options where the real answer
  might be a third thing, and never float an example number for a price, deposit or
  budget — your figure contaminates theirs. NEVER paraphrase a rule you inferred and
  invite a yes/no; ask for the actual trigger in their own numbers ("which bookings
  need the deposit — a price cutoff, certain services, or new customers only?").
  Ask the general version of a question, not one case's version.
- QUANTIFY VAGUE WORDS on the spot, one follow-up each: "sometimes" → how often;
  "we charge more in season" → how much more, which months; "maybe raising prices" →
  how much, what's driving it, when; "closed for New Year" → which dates; "small
  money" → a figure. A vague adverb is not an answer a build can use.
- RELEVANCE FILTER: ask only what could change THIS build. Skip whole topics the
  business model rules out (mail-in service for a walk-in salon, payment collection
  for a bot that never touches money) rather than harvesting a "not offered". If the
  owner questions a topic's relevance, drop it and move on.
- Read the room. With a terse, busy or impatient owner, cut rapport and biographical
  questions entirely. For plain list items (a menu, a service list, staff) ask for
  the whole list in ONE turn instead of item by item.
- When something surfaces in passing — a side dilemma, a new customer type, a
  machine, a pending purchase, a hesitation, a second location, a possible hire —
  follow it up immediately with one direct question, including what it means for
  THIS build (in scope? what does it cost? does the schedule or price list need to
  support it?), and file it in its own field. "Not yet asked" is never acceptable
  for something they raised themselves. If they voice the same worry or decision
  twice, treat it as live: ask the consequence for the project and record it as
  pending (plus adoption_risks) even if they gave a "for now" answer.
- DEFERRED PROMISES: when they say "I'll tell you in a second", "I'll send it later"
  or similar, record it in deferred_owner_commitments THAT TURN (item, their words in
  promised_as, how_delivered) and come back for it before wrap-up. Set received true
  the moment they supply it. If it still isn't there, make one concrete ask rather
  than a passive "send it whenever". An unkept promise blocks completion once.
- If you notice an inconsistency or unrealistic expectation, point it out politely
  ONCE inside your next question; accept whatever they answer.
- You capture requirements; you do not scope, price, or promise feasibility. Never
  tell the owner what the bot "can" do — say you're noting it for the build team.
- Converse in the owner's language, but always WRITE THE FORM IN ENGLISH (keep a
  short original-language quote in brackets when the wording matters).
- PACING: aim to finish in roughly 30–40 exchanges. A gap that yields "I don't know
  yet" gets ONE follow-up at most: record it as unknown and move on. Never spend
  consecutive turns on what they've shown they can't answer. If they signal
  impatience, drop secondary topics and go to wrap-up.

ORDER OF WORK
Start from the owner's own headline want ("what do you actually want this thing to do
for you?") and settle the scope decision. THEN drill the mechanics of every feature
they endorsed — that is the build. THEN the catalog and the rest of the essentials.
Budget and timeline come once scope is clear, not in the opening turns — but well
before wrap-up. Never let a detail question displace what they actually asked for.

FEATURE MECHANICS (the endorsed features must leave the interview buildable)
For each feature the owner agrees to, establish what triggers it, who or what supplies
the data, who receives the output, what the bot says, and what happens when it fails.
Never park an agreed feature as a pending design decision while the owner is right
there and willing to specify it.
- SCHEDULING / BOOKING: beyond price and duration, the concurrency model — how many
  customers can be served at once, how many chairs/rooms/bays/stations, which staff
  can perform which line on which days and hours, who confirms, how walk-ins land,
  cancellations. Two bookings in the same hour may be perfectly fine; you cannot know
  until you ask. All of it goes in scheduling_requirements (bookable_services,
  slot_length, concurrency, resources, who_confirms, booking_lead_time,
  walk_in_handling, cancellation_policy, no_show_policy), plus concurrent_capacity
  for the single how-many-at-once number. Scheduling in solution_scope with
  scheduling_requirements null is a feature asserted with no specification.
- OUTBOUND MESSAGES / REMINDERS: what fires each one and how the human tells the
  system (a part arrived, a reply is overdue), who receives it, lead time, repeats,
  confirm/cancel wording, opt-out, quiet hours, and HOW it is delivered and to whom
  (reminder_channel, reminder_recipients) — all in notification_settings. A reminder
  with no channel and no recipient is not a design decision to park, it is one
  question you can ask right now.
- LEAD CAPTURE: what the bot must collect from an enquiry before a human can answer
  (date, venue, quantity, vehicle, budget) — derive it from how the owner themselves
  works out a quote, and record one lead_capture_fields entry per item: "<what to
  collect> — <why it is needed> — <required | optional>".
- LIVE DATA: any feature that answers from a schedule, stock level or price list
  needs a source. Where does it live today, what tool holds it (integrations), who
  enters what doesn't come through the bot, and who updates the content after launch
  (maintenance_and_ownership). "It's all in my head" plus a pending price change is a
  maintenance requirement, not a fun fact.
- DELIVERY CHANNEL: confirm the channel actually exists and where it runs (if it's a
  website — does the site exist, what platform/host, who holds the login). One
  channel_readiness entry per channel in channels, with readiness "exists" /
  "planned" / "does not exist yet". "Website, maybe" is not a deployment target; the
  build team must be told the site has to be built first. Every channel they mention
  in passing gets one line confirming it in or out — if it is still unsettled at
  wrap-up it goes in channels_considered_not_in_scope with in_scope null, never into
  additional_notes.

WHAT YOU MUST COVER (ask directly — never infer, never leave for later)
ESSENTIALS:
- SCOPE: what the bot should do, and whether that is something simple (reminders,
  replies, FAQ) or a full booking/ordering/payment flow. Record in solution_scope.
- BUDGET and TIMELINE. A vague answer gets ONE follow-up for a figure they wouldn't
  want to go past and any hard date or season driving it. Record their words in
  budget AND the number in budget_amount_min / budget_amount_max / budget_currency,
  with budget_confidence "firm", "estimate", "refused" or "scope-dependent — <what it
  depends on>". One figure goes in both min and max. Asked and refused is a real
  answer — write "refused" rather than null.
- THE FULL CATALOG SWEEP: walk through every service or product line they offer, each
  with its OWN price question — including the everyday menu or price list, even when
  the conversation has been about something bigger. "It depends, I can't quote that"
  closes only THAT line; continue to the next. Never let one broad pricing answer
  stand for several lines, and never build a catalog entry from a single transaction.
  The everyday price list goes in menu_items (one object per item, with price, unit
  — "2 for $5" is a unit, not a price — variants, dietary_flags);
  services_and_pricing is for service lines, tiers and surcharges. A food or retail
  business with empty menu_items was never walked through its own menu.
- For each bookable line: how long it takes and the turnaround / lead time customers
  ask about (service_durations, lead_time) — asked in the same breath as the price,
  never left null after asking ("unknown-varies — <reason>"). When you record a
  duration for a line, fill that line's lead_time too rather than leaving it null:
  for an in-person appointment the duration IS the turnaround, and line_type
  "appointment" says so.
- MONEY MECHANICS where money is in play: deposits, prepayment, cancellation/refund
  rules per line, and how consistently they're actually enforced. Plus the PAYMENT
  RAIL — what instrument actually takes the money today. Never specify a
  money-handling feature whose rail you haven't asked about; equally, skip this
  entirely for a bot that never touches money.
  * payment_methods — every rail accepted today WITH its handle and limits, one per
    entry: "Venmo — @tacoslacamachito", "cash — preferred", "card — not accepted, no
    reader". Their only home; never inside faq_answers or business_policies.
  * payment_collection_requirements — only when the BOT itself would take or request
    money: rail, provider, who_reconciles, trigger, refund_handling. A deposit needs
    a NUMBER, not a sentence: deposit_amount, and where it kicks in as
    deposit_threshold_amount plus deposit_threshold_currency ("we ask on the bigger
    jobs" → "bigger from what?"), plus exempt_segments for who never pays it.
- LANGUAGES the bot must handle. Ask outright — never infer from the interview's
  language, and never leave null.
- LOCATION and service area, into locations — one entry each for the full street
  address including city and region, any alternate site, and the service radius with
  what distance does to price or availability. A brief with no city cannot be built
  against; if they can't give the address now, secure a concrete commitment for it.
- HOURS and days, into operating_hours — one entry per day or day-range with closed
  days stated EXPLICITLY ("Tue-Sat — 10:00-18:00", "Sun, Mon — closed"), plus
  holiday_closures with actual dates, plus any pattern of early closing (how often).
- CAPACITY: how much work they can really handle, in what window, with whose help.
  Record the owner's rules of thumb verbatim in capacity_constraints, the
  how-many-at-once number in concurrent_capacity, and for anywhere people sit down
  (restaurant, cafe, salon, clinic) the actual seats, covers and table sizes in
  venue_capacity — "can you fit 8 of us?" is a question the bot will get daily.
- VOLUME: how many inquiries/messages they get and when they spike
  (conversation_volume) — the only sizing signal the build team gets.
- TEAM: one team_members entry per person — name, role, relationship, the skill or
  limitation that decides what they may be given (skills_or_limitations), which lines
  they perform (services_performed), days/hours, and whether the bot may book them
  (can_book) or hand a customer to them (can_answer_customer_questions), in their own
  terms ("Beto Jr — son, 17, in school, weekends only"). Never reduce a person to
  "helper", never leave them in business_description prose, capacity_constraints or
  additional_notes, and never characterise them more warmly than they did.
- BACKUP / ESCALATION: who handles an urgent question when the owner is asleep or
  away, or "nobody, it waits until morning". Split strictly:
  * escalation_contacts — STRUCTURED CONTACTS ONLY, "<name/role> — <number or
    channel> — <when they cover>", or the single entry "none".
  * escalation_rules — what gets handed over and what the bot should do. "No backup
    exists" is itself a rule. Not null once discussed.
- CURRENT TOOLING AND DATA SOURCES, into current_systems_and_records — one entry per
  system: where the information the bot needs lives today (paper, Excel, a phone, an
  ERP), what it holds, who maintains it, whether it's backed up or reachable.
- SUCCESS CRITERIA — its own question: "what would make you say this was worth it?"
  Push once for something concrete, then record their own words. Never null.
- OWNER CONCERNS: ask outright at least once what worries them about handing this to
  a bot. "None captured" is a failure of asking, not a finding.
THEN:
- CUSTOMER SEGMENTS: do all orders arrive the same way, or do offices, shops or
  repeat commercial accounts order differently from walk-ins? Each segment with its
  own rules (how the order arrives, minimums, lead time, payment). Never let a B2B
  account survive as one sentence in business_description.
- EQUIPMENT AND CAPACITY ASSETS — the physical things that set the ceiling, with
  count, make and age ("5 x Deckel milling machines — oldest from 1994"). Only the
  RULES they imply ("two jobs a day, no more") go in capacity_constraints.
- ADJACENT ASKS — anything they want that is NOT the chatbot (a website, a POS, an
  app), in their own words with why they raised it and any cost or urgency. Don't
  promise it, don't talk them out of it; an unrecorded ask vanishes.
- PENDING PURCHASES, into capex_and_equipment_decisions — kit they are weighing but
  haven't bought. Ask the cost and the state of the decision the MOMENT they mention
  one: "second generator — $1500 — undecided — would let them take the Sunday
  market". Not what they already own, not work they want beyond the bot.
- REMOTE / MAIL-IN SERVICE, into remote_service_policy, when the business could
  plausibly work that way: ask the open version ("who sends things in rather than
  coming by?"), then domestic and international separately — who pays shipping,
  turnaround, customs, insurance, what the bot should say.
- What customers most commonly ask about besides the main use case, so faq_answers
  covers what the bot will inevitably receive.
- The owner's pains and unresolved business decisions (margins, undercharging,
  staffing, a possible second site, a new service line they haven't priced, rising
  costs). A decision they have genuinely NOT made goes in open_strategic_decisions
  with the options as they framed them, what it hinges on, and what it would change
  for the bot — not in pricing_uncertainties (that is for a price they cannot state)
  and not left to the auto-generated gaps, which only ever emit templated ones.
- Brief business background: how long they've run (years_in_business) and how it
  started (founding_story) — the bot gets asked both. Who's involved goes in
  team_members or stakeholders.
- Secondary — when relevant and they're still engaged: stakeholders (a doubter is a
  project risk), certifications and standards, upcoming changes in the next year,
  marketing channels and ad budget (including any marketing question they ask you,
  with your honest short answer), partners and referrals, post-launch ownership.

RECORDING THE FORM
- GROUNDING: never write a fact, figure or requirement unless the owner explicitly
  stated it here, or it is source-flagged in the analyzed materials. If a topic hasn't
  come up, ask — never invent a plausible value. If they correct something, drop the
  old version entirely.
- Record every hard fact the moment you learn it, in the owner's exact names and
  figures (full business name, exact prices — never paraphrased):
  * services_and_pricing — one OBJECT per line/tier with name, price, price_basis
    ("fixed", "from", "range", "quote only"), minimum_order_quantity,
    fee_trigger_condition, lead_time and line_type. A stated BAND is "range" with
    price_min and price_max both filled, and what moves it within the band in notes —
    never flatten a band to "from". Every surcharge and seasonal uplift is its own
    entry with its own trigger ("rush fee +30%" is unusable until you ask what counts
    as rush and what turnaround it buys). Never conflate one line's rate into another's.
    ALWAYS set line_type — "deliverable", "appointment", "discount", "inclusion",
    "add-on", "surcharge" or "fee". It is what stops a "Referral discount" line from
    being chased for a turnaround and a price band it can never have.
    A concrete price seen or quoted for an existing line ("knotless braids, medium,
    shoulder length — $180") is a price_examples entry ON THAT LINE, never a new
    service row: promoting one duplicates the parent line and invents open items.
  * pricing_uncertainties — prices that cannot be stated at all, with the reason,
    whether the bot may quote it, and any known pending change with its size and
    timing.
  * service_durations — how long each bookable line takes ("alignment — 45 min").
  * faq_answers, business_policies, escalation_rules, solution_scope,
    business_description, constraints (hard limits only: compliance, sign-offs, data
    they cannot share, technical limits).
  * channels — only channels the bot should actually be deployed on.
  * Anything important that fits no field goes in additional_notes — never drop a
    fact for lack of a field.
- Preserve nuance in their words (what's "supposed to" happen vs. what does, genuine
  ambivalence) rather than flattening it into a binary or a settled "no plan".
- Feelings are NOT constraints and NOT open items:
  * adoption_risks — what a builder must design around: discomfort with automation,
    distrust of card payments, fear of losing the human touch, fear of a key person
    leaving.
  * background_color — human detail that changes no build decision.
  Record each the turn you hear it, with the reason they gave. An interview ending
  with both empty has lost signal, not found none.
- "IT VARIES" IS AN ANSWER. A null field means the question was never asked, so never
  leave a field null after asking. When genuinely indeterminate, write one entry
  starting with the literal token unknown-varies plus the reason and any rule that
  does hold. When they don't know YET and someone must look it up, start with the
  literal token unknown - pending confirmation ("unknown - pending confirmation — Ute
  knows which standards we hold").
- ONE FACT, ONE ENTRY. Never record the same customer, price or upload fact twice,
  never concatenate one field into another, never copy one field into another, and
  never write truncated cross-reference stubs. Leave owner_sentiment_or_concerns and
  open_items empty — both are rebuilt automatically from the fields below.

FOLLOW-UPS — ROUTE BY WHO MUST ACT
- Try to answer it yourself first: ask the owner directly at least once before parking
  anything. A follow-up is for genuine unresolved uncertainty ("not sure",
  flip-flopping, can't decide) — never for a question you simply didn't ask.
- unresolved_business_facts — a fact only the OWNER can supply and hasn't. Note they
  were asked.
- pending_design_decisions — a decision about the BOT still open (tone, when to hand
  over, whether to quote prices). Not a home for mechanics you could have nailed down.
- customer_replies_owed — a real customer in the materials still waiting. When you
  surface one, also nudge the owner to reply to them now. END every entry with
  "— status: confirmed | disputed_by_owner | unverified": an item the owner disputed
  is a disputed item, not an action item, and one that says so in fact_conflicts must
  say so here too instead of quietly staying on the delivery team's list.
- deferred_owner_commitments — something the OWNER said they would supply in a
  moment or send later. File it there, not in unresolved_business_facts: it is still
  chaseable while they are on the call, and anything still outstanding at the end is
  copied into unresolved_business_facts for you.
- out_of_scope_asides — the business's own decisions nobody on the delivery team
  should action. Before filing one here, check it has no build implication.
- Capacity limits go in capacity_constraints, feelings in adoption_risks or
  background_color, money limits in budget, unstateable prices in
  pricing_uncertainties, marketing questions in marketing_channels_and_ad_budget.
- NEVER invent an uncertainty. If they never wavered, it is not pending.

PROVENANCE AND CONFLICTS
- ATTRIBUTE, ALWAYS. Anything from the uploads is not something the owner told you.
  Raise it as theirs to check: "the chat log you shared from 14 March shows X — is
  that right?" Never state it flatly and never say "you said". Unattributed upload
  detail reads as invention and costs you the interview.
- An entry tagged "[source: uploaded_materials, verified: false]" is an UNVERIFIED
  LEAD, not a fact. It may never enter a normal field, a price or policy line, a
  confident statement or the wrap-up summary until the owner confirms it. Prices,
  payment methods, hours, channels, languages, volumes and team size are the ones
  that bite — verbally verify every upload fact that will drive a bot answer.
- ONE tag per entry, at the START of the line, exactly one entry per fact: carry the
  line forward and edit its tag in place to "verified: true" when confirmed, then
  write the fact into its normal field. Never append a second tag, a "(unverified)"
  copy, or a re-listed duplicate.
- If the owner challenges where the files came from or whether you should have them
  at all, that is not one note among many: record it once in
  upload_provenance_status ("owner disputes the provenance/authorization of the
  uploaded chat logs — <their words>"), because it puts every upload-derived fact in
  the brief in doubt at once. "accepted as theirs" and "not discussed" are the other
  two values. Never leave that verdict in additional_notes.
- If the owner contradicts an upload fact, don't fold and don't silently delete:
  quote the source once with its date and ask them to reconcile ("on 02/01 you quoted
  Meera 15–20k for pre-wedding — is that still your number?"). Record fact_conflicts
  as "<topic> — materials: '<quote>' vs. owner: '<quote>' — <resolved to X /
  unresolved>". Accept their answer after that one ask.
- Never turn one observed transaction into a catalog or policy entry.

WRAP-UP AND COMPLETION
- When the important fields are filled, summarize everything back in next_message —
  problem, audience, channels, integrations, volume, success criteria,
  booking/payment mechanics including the rail, policies, scope decision — and ask
  them to confirm or correct. The summary may contain ONLY facts the owner stated or
  explicitly confirmed, in their own characterisation. Adding, upgrading or slipping
  in an unverified upload detail here makes them correct you instead of confirming.
- BEFORE you set interview_complete, sweep once:
  1. Misrouting: read additional_notes and every long free-text field and move
     anything sitting in the wrong home into its own field (a worry into
     adoption_risks, an open bot decision into pending_design_decisions, a person
     into team_members or stakeholders, a bulk account into customer_segments, a
     channel they floated into channels_considered_not_in_scope, an undecided
     business question into open_strategic_decisions, a promise they made into
     deferred_owner_commitments). A field null only because nothing was routed into
     it is a defect.
  2. Essentials check: if any ESSENTIAL is still null — budget, timeline, languages,
     location, hours and closed days, the full catalog and durations, volume, the
     payment rail for any money feature, current data sources, integrations for any
     live-data feature, capacity, escalation, success_criteria — plus any deferred
     promise, ASK now, batched into one or two short closing questions, or get
     explicit permission to park them. BUDGET, TIMELINE and SOLUTION_SCOPE are
     hard-gated: the brief cannot be emitted while any is null. Asked and refused
     passes the gate; never asked does not.
  3. Duplicates: remove any fact recorded twice.
- Only after they confirm, set interview_complete true and make next_message a brief
  thank-you stating what happens next. It must be false on every other turn — never
  on a message that asks a question, never before they confirmed the summary.
- TERMINATION: the moment the owner signals they're done — confirms, says goodbye,
  asks for a call, or stops cooperating — move every still-unanswered essential into
  the matching follow-up field marked as never discussed, give ONE short closing
  message, and set interview_complete true that same turn. If essentials are missing,
  that message must make ONE concrete, cheap ask ("when you get a second, text me
  your budget range and your deadline"). If they send more, keep next_message minimal
  and interview_complete true. One goodbye ends the interview; it is not a loop.

ASKING FOR REAL MATERIALS (part of the interview)
- Early on, once you understand their problem and channels (and no analysis appears
  below yet), ask them to share real customer conversations — chat exports or
  screenshots — explaining briefly that real inquiries let you ask better questions.
  Tell them to put the files in the folder named 'uploads' next to this program and
  say when they're done. If uploading is awkward, they can paste a few examples.
- When they say the files are in place, set run_file_analysis to true and make
  next_message a short "give me a moment to study them" note.
- If they decline or have nothing, continue without pressing; never ask twice.
- The uploads folder is scanned every turn, so an analysis can appear even when they
  said they had nothing. If so, trust the analysis, mention naturally that you found
  their files, and work through it as usual.

=== ANALYSIS OF THE OWNER'S SHARED MATERIALS ===
{analysis}
================================================
If materials were analyzed above:
- Treat facts_learned and resolved_patterns as already answered; fill the form from
  them and do NOT ask about them — except any fact that will drive a bot answer,
  which you confirm out loud, with attribution.
- Work the knowledge_gaps and notable incidents AFTER the essentials, one per turn,
  always attributed to the files and citing date and detail, then asking whether it
  is typical and what should have happened ("the message in the log you shared from
  14 March sat three weeks before a reply — is that usual, and how should the bot
  handle it?"). Ask the general version of the incident, not just that customer's
  case. Turn each answer into services_and_pricing, business_policies,
  escalation_rules/contacts, capacity_constraints or faq_answers. Confirm each entry
  already in customer_replies_owed and add any the analysis missed. Cover the
  incidents before completing, unless essentials are still open or the owner
  disengages — those come first.

Every turn, output the FULL updated requirements form IN ENGLISH (carry forward
everything already learned; the conversation history shows your previous outputs) —
English applies to every field on every turn, whatever language the conversation is
in. Wrap your entire output in this format and provide no other text
{format_instructions}
```

---

### `persona_test.py`

```python
# persona_test.py — manual persona testing for RequirementsBot.
#
# A human (or an agent) role-plays a business-owner persona and talks to the
# bot one message at a time. Each session keeps its full state on disk, so a
# conversation can span many separate command invocations — which is exactly
# how an agent drives it.
#
# Usage:
#   python persona_test.py new <session>              start a session, print the greeting
#   python persona_test.py say <session> "<message>"  send one owner message, print the reply
#   python persona_test.py transcript <session>       print the whole conversation so far
#   python persona_test.py form <session>             print the latest requirements form
#   python persona_test.py list                       list sessions
#
# To test file analysis: put chat exports / screenshots into uploads/ before
# telling the bot the files are ready — it decides when to run the analysis.
# When the bot marks the interview complete, the final brief is printed and
# saved into the session file under "brief".
import json
import sys
from pathlib import Path

from requirements_bot import RequirementsBot

ROOT = Path(__file__).parent
SESSIONS_DIR = ROOT / "persona_sessions"


class PersonaSession:
    """One persisted interview: a RequirementsBot plus its transcript on disk."""

    def __init__(self, name: str, state: dict):
        self.name = name
        self.transcript: list[list[str]] = state["transcript"]
        self.form: dict | None = state["form"]
        self.brief: dict | None = state["brief"]
        self.bot = RequirementsBot.from_dict(state["bot"])

    # ---------------- persistence ----------------
    @staticmethod
    def _path(name: str) -> Path:
        return SESSIONS_DIR / f"{name}.json"

    @classmethod
    def create(cls, name: str) -> "PersonaSession":
        session = cls(name, {
            "bot": RequirementsBot().to_dict(),
            "transcript": [["Bot", RequirementsBot.GREETING]],
            "form": None,
            "brief": None,
        })
        session.save()
        return session

    @classmethod
    def load(cls, name: str) -> "PersonaSession":
        path = cls._path(name)
        if not path.exists():
            sys.exit(f"no session named '{name}' — start one with: "
                     f"python persona_test.py new {name}")
        return cls(name, json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        SESSIONS_DIR.mkdir(exist_ok=True)
        self._path(self.name).write_text(
            json.dumps({
                "bot": self.bot.to_dict(),
                "transcript": self.transcript,
                "form": self.form,
                "brief": self.brief,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8")

    # ---------------- one conversational turn ----------------
    def say(self, message: str) -> None:
        if self.bot.complete:
            sys.exit("this interview is already complete — see: "
                     f"python persona_test.py form {self.name}")

        messages, turn = self.bot.send(message)
        self.transcript.append(["Owner", message])
        for msg in messages:
            self.transcript.append(["Bot", msg])
            print("Bot:", msg)

        self.form = turn.requirements.model_dump()
        if self.bot.complete:
            self.brief = self.bot.brief(turn)
            print("\n[COMPLETE] final brief:")
            print(json.dumps(self.form, indent=2, ensure_ascii=False))
        self.save()


def cmd_list() -> None:
    if not SESSIONS_DIR.exists():
        print("(no sessions)")
        return
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        state = json.loads(path.read_text(encoding="utf-8"))
        status = "complete" if state["bot"]["complete"] else "in progress"
        turns = len(state["transcript"])
        print(f"{path.stem:20s} {status:12s} {turns} transcript entries")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__ or "see file header for usage")
    cmd = args[0]
    if cmd == "new" and len(args) == 2:
        PersonaSession.create(args[1])
        print("Bot:", RequirementsBot.GREETING)
    elif cmd == "say" and len(args) == 3:
        PersonaSession.load(args[1]).say(args[2])
    elif cmd == "transcript" and len(args) == 2:
        for who, msg in PersonaSession.load(args[1]).transcript:
            print(f"{who}: {msg}\n")
    elif cmd == "form" and len(args) == 2:
        print(json.dumps(PersonaSession.load(args[1]).form, indent=2, ensure_ascii=False))
    elif cmd == "list" and len(args) == 1:
        cmd_list()
    else:
        sys.exit("usage: python persona_test.py new|say|transcript|form|list "
                 "<session> [message]")
```

---

### `parallel_personas.py`

```python
# parallel_personas.py — mass persona testing for RequirementsBot.
#
# Runs MANY simulated interviews at once: for each interview an LLM invents a
# business-owner persona and role-plays them against RequirementsBot until the
# interview completes; a judge then scores the transcript and lists problems.
#
# Each interview is fully independent (its own RequirementsBot, no uploads/
# folder involvement — personas paste example messages instead of files), so
# they parallelize safely with a thread pool.
#
# Usage:
#   python parallel_personas.py --count 10 --concurrency 5
#
# Output: parallel_runs/<stamp>/interview_<n>.json (persona, transcript, brief,
# evaluation) plus summary.json with scores and aggregated findings.
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from requirements_bot import RequirementsBot, _blocks_to_text

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
# No scripted turn limit: an interview ends when the bot completes or the
# persona leaves ([LEAVES]). The ceiling below is an emergency circuit
# breaker only — it should never fire; hitting it means a runaway loop.
SAFETY_CEILING = 60

# $ per 1M tokens (input, output), Anthropic API rates (cached 2026-06).
# Cache writes bill at 1.25x input, cache reads at 0.10x input.
PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
}


def usd_in_out(model: str, usage: dict) -> tuple[float, float]:
    """(input cost, output cost) in dollars for a usage dict."""
    inp, outp = PRICING.get(model, (5.00, 25.00))  # unknown model: price as Opus
    cost_in = (usage.get("fresh_in", 0) * inp
               + usage.get("cache_write", 0) * inp * 1.25
               + usage.get("cache_read", 0) * inp * 0.10) / 1_000_000
    return cost_in, usage.get("out", 0) * outp / 1_000_000


def usd(model: str, usage: dict) -> float:
    """Total dollar cost of a usage dict {fresh_in, cache_write, cache_read, out}."""
    return sum(usd_in_out(model, usage))


def add_usage(acc: dict, reply) -> None:
    """Accumulate one model reply's usage counters into acc."""
    u = reply.response_metadata.get("usage") or {}
    acc["fresh_in"] += u.get("input_tokens") or 0
    acc["cache_read"] += u.get("cache_read_input_tokens") or 0
    acc["cache_write"] += u.get("cache_creation_input_tokens") or 0
    acc["out"] += u.get("output_tokens") or 0


def empty_usage() -> dict:
    return {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}


_print_lock = threading.Lock()
_log_file: Path | None = None  # set per run; dashboard.py tails it


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)
        if _log_file:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")


# ---------------- persona ----------------
class Persona(BaseModel):
    owner_name: str
    business_name: str
    industry: str
    personality: str          # a distinct, challenging conversational style
    background_facts: str     # everything the persona knows: services, prices, policies, problems
    whatsapp_export: str      # a realistic fake chat export placed in the interview's uploads dir


class PersonaBatch(BaseModel):
    personas: list[Persona]


PERSONA_BATCH_PROMPT = """You are creating fictional business owners to test a
requirements-gathering chatbot. Create {n} COMPLETELY DIFFERENT personas —
vary country, industry, business size, formality, and language style.

For each persona:
- a realistic owner and business
- personality: a distinct, challenging conversational style (examples: rambles
  off-topic, one-word answers, changes their mind, mixes two languages, vague
  about numbers, suspicious of technology, impatient, oversharing). One main
  trait each, all different from each other.
- background_facts: a rich private fact sheet the persona draws answers from —
  services with prices, hours, policies, staff, pain points, budget, timeline.
  Include 1-2 things the owner is genuinely unsure about.
- whatsapp_export: a fake WhatsApp export (15-25 messages, realistic timestamps)
  between customers and the business. Include BOTH clearly-answered inquiries
  AND 2-3 dead-ends where staff said "let me check" or never followed up.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


PERSONA_TURN_PROMPT = """You are role-playing {owner_name}, owner of {business_name}
({industry}), talking to a chatbot consultant on a text chat.

Your personality: {personality}
Your private knowledge (answer ONLY from this; say you're not sure otherwise):
{background_facts}

Rules:
- Stay in character; reply the way this person types (length, tone, quirks).
- Answer the consultant's LAST message. One reply only, no narration.
- If asked to put files in an 'uploads' folder, reply that you added them
  (the files are already in place).
- If the consultant summarizes everything and asks you to confirm, check it
  against your knowledge: correct at most one or two real mistakes, otherwise
  confirm clearly.
- You can LEAVE the chat at any moment, exactly like a real person closing
  the chat window: when the interview feels finished, or you're out of
  patience, or you've said your goodbyes and have nothing to add, end your
  reply with the exact token [LEAVES]. After that the chat is over — so
  don't keep exchanging pleasantries forever; leave like a busy owner would.

Conversation so far:
{transcript}

Your reply:"""


# ---------------- evaluation ----------------
class Finding(BaseModel):
    problem: str              # the specific observed problem
    excerpt: str              # the exact transcript lines where it happened, copied verbatim


class Evaluation(BaseModel):
    fact_capture: int         # 0-10: did every hard fact the owner gave land in the brief?
    no_repeats: int           # 0-10: never asked about things already answered
    naturalness: int          # 0-10: warm, human, adapted to the owner's personality
    completeness: int         # 0-10: brief usable by a developer; unknowns in open_items
    efficiency: int           # 0-10: no wasted/low-value questions; finished in sane turns
    findings: list[Finding]   # specific observed problems, each with its transcript excerpt
    top_improvement: str      # the single most valuable change to the interviewer prompt
    code_suggestions: list[str]  # STRUCTURAL problems prompt wording cannot fix (missing form
                                 # fields, crashes, missing capabilities) — fixed by a coding agent


EVAL_PROMPT = """You are a strict QA judge for an AI interviewer ("RequirementsBot")
that gathers chatbot requirements from business owners. Judge THIS interview.

The persona's private fact sheet (what the owner knew and could have shared):
{facts}

The materials the owner "uploaded" (the interviewer analyzed these; facts in
the brief may legitimately come from here, not only from the transcript):
{materials}

The interviewer's analysis of those materials:
{analysis}

The transcript:
{transcript}

The final requirements brief produced:
{brief}

Interview completed: {completed}, ended by: {ended_by} (in {turns} transcript
entries; fewer is better, ~20-40 is normal). There is no turn limit: an
interview ends when the bot completes or the owner leaves. "owner_left" with a
rich brief and unanswered essentials parked in open_items is acceptable —
judge how well the bot used the time it got and whether it wrapped up
gracefully; "safety_ceiling" means a runaway loop, a serious failure.

Score each rubric dimension 0-10 harshly. In findings, list concrete problems;
for EACH finding, copy into its excerpt the exact transcript lines (speaker names
included, 1-4 lines) where the problem occurred — verbatim, no paraphrasing.
Compare the fact sheet against the brief for lost facts. Then name the ONE most
valuable prompt improvement.

Separately, in code_suggestions, list STRUCTURAL problems that prompt wording
cannot fix — e.g. a kind of fact that recurringly has no proper form field
(check the brief's additional_notes and open_items for facts crammed somewhere
wrong), a crash or malformed output, or a capability the interviewer lacks
entirely. Suggest the field/capability to add. Empty list if none.

Wrap your entire output in this format and provide no other text
{format_instructions}"""


class ParallelPersonaRunner:
    """Generates personas, runs their interviews concurrently, judges them."""

    def __init__(self, count: int, concurrency: int,
                 persona_model: str = "claude-sonnet-5",
                 judge_model: str = "claude-opus-5"):
        self.count = count
        self.concurrency = concurrency
        self.persona_model = persona_model
        self.judge_model = judge_model
        # One shared client for personas/judging is fine — invoke() is thread-safe.
        from langchain_anthropic import ChatAnthropic
        self.persona_llm = ChatAnthropic(model=persona_model, max_tokens=8000,
                                         max_retries=6)
        # Judge/improver budget must cover extended thinking PLUS a full
        # ~8000-char prompt rewrite — 8000 tokens proved too tight (the
        # improver's text came back truncated after long thinking).
        self.judge_llm = ChatAnthropic(model=judge_model, max_tokens=16000,
                                       max_retries=6)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = RUNS_DIR / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        global _log_file
        _log_file = self.run_dir / "run.log"
        (RUNS_DIR / "latest.txt").write_text(str(self.run_dir), encoding="utf-8")

    # ---------------- pipeline steps ----------------
    def generate_personas(self) -> list[Persona]:
        """Batch-generate personas, 10 per model call. Each batch retries on
        malformed output (e.g. unescaped quotes inside a JSON string) — one
        bad character must not kill a whole run before it starts."""
        parser = PydanticOutputParser(pydantic_object=PersonaBatch)
        personas: list[Persona] = []
        self.persona_gen_usage = empty_usage()
        log(f"[personas] model {self.persona_model}")
        while len(personas) < self.count:
            n = min(10, self.count - len(personas))
            last_error = None
            for attempt in range(1, 4):
                reply = self.persona_llm.invoke(PERSONA_BATCH_PROMPT.format(
                    n=n, format_instructions=parser.get_format_instructions()))
                add_usage(self.persona_gen_usage, reply)
                try:
                    batch = parser.parse(_blocks_to_text(reply.content)).personas[:n]
                    break
                except Exception as e:
                    last_error = e
                    log(f"[personas] batch attempt {attempt} unparseable, retrying...")
            else:
                raise last_error
            personas.extend(batch)
            log(f"[personas] {len(personas)}/{self.count} generated")
        g = self.persona_gen_usage
        gen_in = g["fresh_in"] + g["cache_read"] + g["cache_write"]
        self.persona_gen_cost = round(usd(self.persona_model, g), 4)
        gi, go = usd_in_out(self.persona_model, g)
        log(f"[personas] generator[{self.persona_model.replace('claude-', '')}] "
            f"in {gen_in:,} (${gi:.2f}) out {g['out']:,} (${go:.2f}) "
            f"total ${self.persona_gen_cost:.2f}")
        return personas

    def run_interview(self, idx: int, persona: Persona) -> dict:
        # Each interview gets its OWN uploads folder so parallel runs never
        # fight over one shared directory; the persona's fake export goes there.
        uploads = self.run_dir / f"uploads_{idx:03d}"
        uploads.mkdir(exist_ok=True)
        (uploads / "whatsapp_export.txt").write_text(
            persona.whatsapp_export, encoding="utf-8")
        bot = RequirementsBot(uploads_dir=uploads)
        transcript = [("Bot", RequirementsBot.GREETING)]
        tag = f"[{idx:03d} {persona.industry[:30]}]"
        ended_by = "safety_ceiling"
        persona_usage = empty_usage()

        for turn_no in range(1, SAFETY_CEILING + 1):
            convo = "\n".join(f"{who}: {msg}" for who, msg in transcript)
            reply = self.persona_llm.invoke(PERSONA_TURN_PROMPT.format(
                owner_name=persona.owner_name, business_name=persona.business_name,
                industry=persona.industry, personality=persona.personality,
                background_facts=persona.background_facts, transcript=convo))
            add_usage(persona_usage, reply)
            owner_msg = _blocks_to_text(reply.content).strip()
            owner_left = "[LEAVES]" in owner_msg
            owner_msg = owner_msg.replace("[LEAVES]", "").strip()
            transcript.append((persona.owner_name, owner_msg))

            # The owner's last words still reach the bot so it can finalize
            # the form; after that the chat window is closed.
            messages, turn = bot.send(owner_msg)
            for msg in messages:
                transcript.append(("Bot", msg))
            running = (usd(bot.model, bot.usage)
                       + usd(self.persona_model, persona_usage))
            in_tok = sum(bot.usage[k] + persona_usage[k]
                         for k in ("fresh_in", "cache_read", "cache_write"))
            out_tok = bot.usage["out"] + persona_usage["out"]
            pm = self.persona_model.replace("claude-", "")
            bm = bot.model.replace("claude-", "")
            b_in, b_out = usd_in_out(bot.model, bot.usage)
            p_in, p_out = usd_in_out(self.persona_model, persona_usage)
            log(f"{tag} turn {turn_no}: owner[{pm}] {len(owner_msg.split())}w"
                f" -> bot[{bm}] {len(messages[-1].split())}w"
                f" | in {in_tok:,} (${b_in + p_in:.2f}) out {out_tok:,} "
                f"(${b_out + p_out:.2f}) total ${running:.2f}"
                + (" [analyzed files]" if len(messages) > 1 else "")
                + (" [COMPLETE]" if bot.complete else "")
                + (" [OWNER LEFT]" if owner_left else ""))
            if bot.complete:
                ended_by = "bot_complete"
                break
            if owner_left:
                ended_by = "owner_left"
                break

        u = bot.usage
        total_in = u["fresh_in"] + u["cache_read"] + u["cache_write"]
        hit = round(100 * u["cache_read"] / total_in) if total_in else 0
        log(f"{tag} bot[{bot.model.replace('claude-', '')}] tokens: "
            f"{u['fresh_in']} fresh + {u['cache_write']} cache-write "
            f"+ {u['cache_read']} cache-read ({hit}% cached) -> {u['out']} out")
        return {
            "transcript": transcript,
            "brief": turn.requirements.model_dump(),
            "analysis": bot.analysis.model_dump() if bot.analysis else None,
            "completed": bot.complete,
            "ended_by": ended_by,
            "turns": len(transcript),
            "usage": u,
            "persona_usage": persona_usage,
            "models": {"bot": bot.model, "persona": self.persona_model,
                       "judge": self.judge_model},
        }

    def evaluate(self, persona: Persona, result: dict) -> tuple[Evaluation, dict]:
        parser = PydanticOutputParser(pydantic_object=Evaluation)
        convo = "\n".join(f"{who}: {msg}" for who, msg in result["transcript"])
        judge_usage = empty_usage()
        reply = self.judge_llm.invoke(EVAL_PROMPT.format(
            facts=persona.background_facts,
            materials=persona.whatsapp_export,
            analysis=json.dumps(result.get("analysis"), indent=2, ensure_ascii=False),
            transcript=convo,
            brief=json.dumps(result["brief"], indent=2, ensure_ascii=False),
            completed=result["completed"], ended_by=result["ended_by"],
            turns=result["turns"],
            format_instructions=parser.get_format_instructions()))
        add_usage(judge_usage, reply)
        return parser.parse(_blocks_to_text(reply.content)), judge_usage

    # ---------------- one full interview + judge ----------------
    def run_one(self, idx: int, persona: Persona) -> dict:
        tag = f"[{idx:03d} {persona.industry[:30]}]"
        try:
            log(f"{tag} interviewing... "
                f"(bot: claude-sonnet-5, persona: {self.persona_model})")
            result = self.run_interview(idx, persona)
            log(f"{tag} completed={result['completed']} ({result['ended_by']}) "
                f"in {result['turns']} entries; "
                f"judging with {self.judge_model.replace('claude-', '')}...")
            ev, judge_usage = self.evaluate(persona, result)
            score = round((ev.fact_capture + ev.no_repeats + ev.naturalness
                           + ev.completeness + ev.efficiency) / 5, 2)
            m = result["models"]
            cost = {
                "bot_usd": round(usd(m["bot"], result["usage"]), 4),
                "persona_usd": round(usd(m["persona"], result["persona_usage"]), 4),
                "judge_usd": round(usd(m["judge"], judge_usage), 4),
            }
            cost["total_usd"] = round(sum(cost.values()), 4)
            record = {
                "index": idx,
                "persona": persona.model_dump(),
                "interview": result,
                "evaluation": ev.model_dump(),
                "judge_usage": judge_usage,
                "cost": cost,
                "score": score,
                "error": None,
            }
            j_in = sum(judge_usage[k] for k in ("fresh_in", "cache_read", "cache_write"))
            ji, jo = usd_in_out(self.judge_model, judge_usage)
            log(f"{tag} judge[{self.judge_model.replace('claude-', '')}] "
                f"in {j_in:,} (${ji:.2f}) out {judge_usage['out']:,} (${jo:.2f}) "
                f"total ${cost['judge_usd']:.2f} "
                f"| score {score}/10, {len(ev.findings)} findings")
            log(f"{tag} cost: ${cost['total_usd']:.2f} "
                f"(bot {m['bot']} ${cost['bot_usd']:.2f} + "
                f"persona {m['persona']} ${cost['persona_usd']:.2f} + "
                f"judge {m['judge']} ${cost['judge_usd']:.2f})")
        except Exception as e:
            record = {"index": idx, "persona": persona.model_dump(),
                      "interview": None, "evaluation": None, "score": None,
                      "error": f"{type(e).__name__}: {e}"}
            log(f"{tag} FAILED: {record['error']}")
        (self.run_dir / f"interview_{idx:03d}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False, default=list),
            encoding="utf-8")
        return record

    # ---------------- orchestration ----------------
    def run(self) -> dict:
        log(f"=== generating {self.count} personas...")
        personas = self.generate_personas()

        log(f"=== running {self.count} interviews, {self.concurrency} at a time...")
        records = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(self.run_one, i, p)
                       for i, p in enumerate(personas, 1)]
            for future in as_completed(futures):
                records.append(future.result())
        records.sort(key=lambda r: r["index"])

        scored = [r for r in records if r["score"] is not None]
        summary = {
            "count": self.count,
            "succeeded": len(scored),
            "failed": len(records) - len(scored),
            "completed_interviews": sum(1 for r in scored if r["interview"]["completed"]),
            "avg_score": round(sum(r["score"] for r in scored) / len(scored), 2) if scored else None,
            "worst": [{"index": r["index"], "industry": r["persona"]["industry"],
                       "score": r["score"],
                       "top_improvement": r["evaluation"]["top_improvement"]}
                      for r in sorted(scored, key=lambda r: r["score"])[:10]],
            "all_scores": {r["index"]: r["score"] for r in records},
            "errors": {r["index"]: r["error"] for r in records if r["error"]},
            "code_suggestions": sorted({s for r in scored
                                        for s in r["evaluation"].get("code_suggestions", [])}),
            "bot_token_usage": {
                key: sum(r["interview"]["usage"][key] for r in scored
                         if r["interview"].get("usage"))
                for key in ("fresh_in", "cache_read", "cache_write", "out")
            },
            "models": {"bot": "claude-sonnet-5", "persona": self.persona_model,
                       "judge": self.judge_model},
            "persona_generation": {
                "model": self.persona_model,
                "usage": getattr(self, "persona_gen_usage", None),
                "cost_usd": getattr(self, "persona_gen_cost", 0.0),
            },
            "total_cost_usd": round(sum(r["cost"]["total_usd"] for r in scored
                                        if r.get("cost"))
                                    + getattr(self, "persona_gen_cost", 0.0), 2),
        }
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"\n=== done: {summary['succeeded']}/{self.count} judged, "
            f"avg score {summary['avg_score']}/10, "
            f"total cost ${summary['total_cost_usd']:.2f} -> {self.run_dir}")
        return summary


class PromptImprover:
    """Applies what the run learned: rewrites interviewer_prompt.txt from the
    aggregated judge findings, then commits and pushes — guardrailed so a bad
    rewrite can never break the bot."""

    IMPROVE_PROMPT = """You maintain the system prompt of "RequirementsBot", an AI
interviewer. Below is its CURRENT prompt, then QA findings from many test
interviews with different business owners. Under each finding, indented lines
starting with | quote the exact chat moment where the problem occurred.

Rewrite the prompt to fix the problems found. STRICT rules:
- Prefer GENERAL principles over specific cases: fix the underlying habit, not
  one business's quirk.
- MERGE overlapping rules and DELETE rules that are redundant, over-specific,
  or implied by a more general rule. A shorter, sharper prompt is a better
  outcome than a longer one.
- Keep everything that clearly works; keep the existing tone and structure.
- The result MUST still contain the literal placeholders {{analysis}} and
  {{format_instructions}} exactly once each.
- Length: there is no hard limit, but shorter prompts follow their rules
  better. Work in two passes: FIRST condense the current prompt ({cur}
  characters) — merge overlapping rules, cut the weakest or most
  over-specific ones — THEN add the most valuable new rules. Prefer
  fixing an existing rule over appending a new one.
- Output ONLY the complete new prompt text. No commentary, no code fences.

=== CURRENT PROMPT ===
{prompt}

=== QA FINDINGS (with chat excerpts) ===
{findings}

=== EACH INTERVIEW'S TOP SUGGESTED IMPROVEMENT ===
{top}
"""

    def __init__(self, llm, run_dir: Path | None = None,
                 model: str = "claude-opus-5"):
        self.llm = llm
        self.model = model
        self.prompt_file = RequirementsBot.PROMPT_FILE
        self.run_dir = run_dir
        self.usage = empty_usage()

    @staticmethod
    def _violation(new: str, current: str) -> str | None:
        """Which guardrail a candidate rewrite breaks, or None if it's fine."""
        if new.count("{analysis}") != 1 or new.count("{format_instructions}") != 1:
            return ("it must contain the literal placeholders {analysis} and "
                    "{format_instructions} exactly once each")
        if len(new) < 500:
            return "it is far too short — output the COMPLETE prompt, not commentary"
        if new == current.strip():
            return "it is identical to the current prompt"
        return None

    def improve(self, records: list[dict]) -> str | None:
        """Rewrite the prompt from the run's findings. Returns a description
        of the change, or None if there was nothing to fix / guardrails hit.
        A rejected attempt is retried with the violated rule quoted back."""
        findings, tops = [], []
        for r in records:
            if not r["evaluation"]:
                continue
            tops.append(f"- {r['evaluation']['top_improvement']}")
            for f in r["evaluation"]["findings"]:
                excerpt = "\n".join("    | " + line
                                    for line in f["excerpt"].splitlines() if line.strip())
                findings.append(f"- {f['problem']}" + (f"\n{excerpt}" if excerpt else ""))
        if not findings:
            return None
        # A big run yields hundreds of findings; past ~80 the improver drowns
        # and bloats. The per-interview top_improvements already summarize.
        if len(findings) > 80:
            findings = findings[:80]

        current = self.prompt_file.read_text(encoding="utf-8")
        base_prompt = self.IMPROVE_PROMPT.format(
            cur=len(current), prompt=current,
            findings="\n".join(findings), top="\n".join(tops))
        feedback = ""
        for attempt in range(1, 4):
            reply = self.llm.invoke(base_prompt + feedback)
            add_usage(self.usage, reply)
            new = _blocks_to_text(reply.content).strip()
            if new.startswith("```"):
                new = new.strip("`").lstrip("text").strip()
            why = self._violation(new, current)
            if why is None:
                self.prompt_file.write_text(new + "\n", encoding="utf-8")
                return f"prompt updated ({len(current)} -> {len(new)} chars, attempt {attempt})"
            log(f"    improve attempt {attempt} rejected: {why}")
            if self.run_dir:
                (self.run_dir / f"rejected_rewrite_{attempt}.txt").write_text(
                    new, encoding="utf-8")
            feedback = (f"\n\n=== YOUR PREVIOUS ATTEMPT WAS REJECTED ===\n"
                        f"Reason: {why}.\nProduce a corrected complete prompt.")
        return None


def _git(*args) -> str:
    import subprocess
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def improve_and_push(runner: "ParallelPersonaRunner", records: list[dict],
                     summary: dict, improve_model: str | None = None) -> None:
    from langchain_anthropic import ChatAnthropic
    model = improve_model or runner.judge_model
    llm = (runner.judge_llm if model == runner.judge_model
           else ChatAnthropic(model=model, max_tokens=16000, max_retries=6))
    log(f"[improve] running with {model}...")
    improver = PromptImprover(llm, run_dir=runner.run_dir, model=model)
    change = improver.improve(records)
    u = improver.usage
    i_in = sum(u[k] for k in ("fresh_in", "cache_read", "cache_write"))
    ii, io_ = usd_in_out(improver.model, u)
    log(f"[improve] improver[{improver.model.replace('claude-', '')}] "
        f"in {i_in:,} (${ii:.2f}) out {u['out']:,} (${io_:.2f}) total ${ii + io_:.2f}")
    log(f"[improve] {change or 'no change (nothing to fix, or guardrails rejected the rewrite)'}")
    if change:
        _git("add", str(RequirementsBot.PROMPT_FILE))
        _git("commit", "-m",
             f"parallel personas x{summary['count']}: avg {summary['avg_score']}/10, "
             f"{change}\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>")
        log("    pushing...")
        log("    " + (_git("push", "origin", "main") or "pushed"))


CODE_FIX_PROMPT = """You are maintaining RequirementsBot in this repository
(requirements_bot.py, models.py, main.py, interviewer_prompt.txt). A mass
persona-testing run of the bot surfaced STRUCTURAL problems that prompt
wording cannot fix. Fix them in code now:

{suggestions}

Rules:
- Read the relevant files first and make the smallest correct change for each
  problem (e.g. a new Optional field on BusinessRequirements in models.py plus
  a short mention in interviewer_prompt.txt, or a bug fix in
  requirements_bot.py). Skip any suggestion that is wrong, already handled,
  or too risky to apply blindly — and say so.
- Never remove existing form fields or break to_dict/from_dict compatibility.
- Verify with a syntax check (python -c "import requirements_bot, models")
  before committing.
- Commit the changes with a clear message ending in
  "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
  and push to origin main. If you changed nothing, commit nothing.
"""


def fix_code_issues(summary: dict, model: str = "claude-opus-5") -> None:
    """Hand the run's structural findings to a headless Claude Code agent that
    edits the code, verifies it, commits and pushes."""
    import shutil
    import subprocess
    suggestions = summary.get("code_suggestions") or []
    if not suggestions:
        log("=== no code-level suggestions from this run.")
        return
    exe = shutil.which("claude")
    if not exe:
        log("[codefix] claude CLI not found; code suggestions saved in summary.json only.")
        return
    log(f"[codefix] running {len(suggestions)} suggestion(s) with {model}...")
    prompt = CODE_FIX_PROMPT.format(
        suggestions="\n".join(f"- {s}" for s in suggestions))
    r = subprocess.run(
        [exe, "-p", prompt,
         "--model", model,
         "--output-format", "json",
         "--permission-mode", "acceptEdits",
         "--allowedTools", "Bash(python*) Bash(git add:*) Bash(git commit:*) Bash(git push:*)"],
        cwd=ROOT, capture_output=True, text=True, timeout=1800)
    text, cost = r.stdout.strip(), None
    try:
        payload = json.loads(text)
        cost = payload.get("total_cost_usd")
        text = payload.get("result") or text
    except (json.JSONDecodeError, AttributeError):
        pass
    if cost is not None:
        log(f"[codefix] agent[{model.replace('claude-', '')}] total ${cost:.2f}")
    log("[codefix] " + (text[-1500:] or "(no output)"))
    if r.returncode != 0:
        log(f"[codefix] agent exited {r.returncode}: {r.stderr.strip()[-500:]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5, help="number of interviews")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="how many run at the same time "
                         "(default: all at once, capped at 25)")
    ap.add_argument("--no-improve", action="store_true",
                    help="only measure; don't rewrite the prompt or push")
    ap.add_argument("--persona-model", default="claude-sonnet-5",
                    help="model that role-plays the business owners")
    ap.add_argument("--judge-model", default="claude-opus-5",
                    help="model that judges transcripts")
    ap.add_argument("--improve-model", default=None,
                    help="model that rewrites the prompt (default: the judge model)")
    ap.add_argument("--fix-model", default="claude-opus-5",
                    help="model for the headless code-fix agent (claude -p)")
    args = ap.parse_args()
    if args.concurrency is None:
        args.concurrency = min(args.count, 25)  # API rate limits, not Python,
                                                # are the ceiling past ~25
    runner = ParallelPersonaRunner(args.count, args.concurrency,
                                   persona_model=args.persona_model,
                                   judge_model=args.judge_model)
    run_summary = runner.run()
    if not args.no_improve:
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted(runner.run_dir.glob("interview_*.json"))]
        improve_and_push(runner, records, run_summary,
                         improve_model=args.improve_model)
        fix_code_issues(run_summary, model=args.fix_model)
```

---

### `dashboard.py`

```python
# dashboard.py — live localhost dashboard for parallel persona runs.
#
# Run:  python dashboard.py            (serves http://localhost:8500)
# Shows the latest run in parallel_runs/ as a 5-stage pipeline:
#   Persona Generator -> Interviewing -> Judge -> Improver -> Fixing Code
# All data is parsed live from run.log; the page polls every 2 seconds.
import json
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
RUNS_DIR = ROOT / "parallel_runs"
PORT = int(__import__("os").environ.get("PORT", 8500))

# "in 45,120 ($0.12) out 3,240 ($0.08) total $0.20" -> stat-box numbers
IO_RE = re.compile(r"in ([\d,]+) \(\$([\d.]+)\) out ([\d,]+) \(\$([\d.]+)\) "
                   r"total \$([\d.]+)")


def parse_io(text: str):
    m = IO_RE.search(text)
    if not m:
        return None
    return {"in_tok": m.group(1), "in_usd": m.group(2),
            "out_tok": m.group(3), "out_usd": m.group(4), "total": m.group(5)}


def _num(tok) -> int:
    try:
        return int(str(tok).replace(",", ""))
    except (ValueError, TypeError):
        return 0


def _usd(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def latest_run_dir():
    pointer = RUNS_DIR / "latest.txt"
    if pointer.exists():
        p = Path(pointer.read_text(encoding="utf-8").strip())
        if p.exists():
            return p
    runs = sorted((d for d in RUNS_DIR.iterdir() if d.is_dir()), reverse=True) \
        if RUNS_DIR.exists() else []
    return runs[0] if runs else None


def collect() -> dict:
    run = latest_run_dir()
    empty_stage = lambda: {"status": "pending", "model": None, "io": None, "note": None}
    pipeline = {"improve": empty_stage(), "codefix": empty_stage()}
    if run is None:
        return {"run_dir": None, "interviews": [], "judges": [],
                "generator": None, "pipeline": pipeline,
                "run_finished": False, "log": "", "started_at": None,
                "last_activity": None}

    log_file = run / "run.log"
    log_text = log_file.read_text(encoding="utf-8", errors="replace") \
        if log_file.exists() else "(this run has no run.log — older run)"

    # run dir name is a timestamp: 20260911_033231
    started_at = None
    try:
        started_at = datetime.strptime(run.name, "%Y%m%d_%H%M%S").timestamp()
    except ValueError:
        pass
    last_activity = log_file.stat().st_mtime if log_file.exists() else None

    interviews: dict[int, dict] = {}
    judges: dict[int, dict] = {}
    generator = {"model": None, "io": None, "done": False}
    run_finished = False

    for line in log_text.splitlines():
        if line.startswith("[personas]"):
            body = line[len("[personas]"):].strip()
            if body.startswith("model "):
                generator["model"] = body[len("model "):]
            elif "generator[" in body:
                generator["io"] = parse_io(body)
                generator["model"] = (generator["model"]
                                      or body.split("generator[")[1].split("]")[0])
                generator["done"] = True
            continue
        if line.startswith("[improve]"):
            body = line[len("[improve]"):].strip()
            st = pipeline["improve"]
            if body.startswith("running with "):
                st.update(status="run", model=body[len("running with "):].rstrip(". "))
            elif "improver[" in body:
                st["io"] = parse_io(body)
            elif body.startswith("prompt updated"):
                st.update(status="done", note=body)
            elif body.startswith("no change"):
                st.update(status="done", note="no change")
            elif "attempt" in body and "rejected" in body:
                st["note"] = body[:60]
            continue
        if line.startswith("[codefix]"):
            body = line[len("[codefix]"):].strip()
            st = pipeline["codefix"]
            if body.startswith("running "):
                st.update(status="run",
                          model=body.split(" with ")[-1].rstrip(". ") if " with " in body else None,
                          note=body.split(" with ")[0].replace("running ", ""))
            elif body.startswith("agent[") and "total $" in body:
                st.update(status="done",
                          io={"in_tok": "—", "in_usd": "0.00", "out_tok": "—",
                              "out_usd": "0.00",
                              "total": body.split("total $")[1].split()[0]})
            elif "claude CLI not found" in body:
                st.update(status="failed", note="claude CLI not found")
            elif "exited" in body:
                st.update(status="failed", note=body[:60])
            elif st["status"] == "run":
                st.update(status="done", note="finished")
            continue
        if line.startswith("=== done"):
            run_finished = True
        if not line.startswith("["):
            continue
        try:
            idx = int(line[1:4])
        except ValueError:
            continue
        industry = line[5:line.index("]")].strip()
        iv = interviews.setdefault(idx, {"index": idx, "industry": industry,
                                         "turn": 0, "status": "interviewing",
                                         "score": None, "findings": None,
                                         "bot_model": None, "persona_model": None,
                                         "bot_cost": None, "persona_cost": None,
                                         "io": None, "log": []})
        iv["log"] = (iv["log"] + [line[line.index("]") + 1:].strip()])[-150:]
        if "interviewing... (" in line:
            m = re.search(r"bot: ([\w.-]+), persona: ([\w.-]+)", line)
            if m:
                iv["bot_model"], iv["persona_model"] = m.group(1), m.group(2)
        if "] turn " in line:
            iv["turn"] = int(line.split("] turn ")[1].split(":")[0].split("/")[0])
            if " | in " in line:
                iv["io"] = parse_io(line) or iv.get("io")
            if "[OWNER LEFT]" in line:
                iv["status"] = "owner left"
        if "; judging with " in line:
            if iv["status"] == "interviewing":
                iv["status"] = "interview done"
            judges[idx] = {"index": idx, "done": False, "io": None,
                           "model": line.split("; judging with ")[1].rstrip(". ")}
        if "] judge[" in line:
            body = line.split("] judge[")[1]
            j = judges.setdefault(idx, {"index": idx, "model": "", "io": None})
            j["model"] = body.split("]")[0]
            j["io"] = parse_io(body)
            j["done"] = True
            if iv["status"] != "owner left":
                iv["status"] = "complete"
            if "| score " in body:
                part = body.split("| score ")[1]
                iv["score"] = part.split(",")[0]
                iv["findings"] = int(part.split(", ")[1].split(" ")[0])
        if "] cost: $" in line:
            m = re.search(r"\(bot [\w.-]+ \$([\d.]+) \+ persona [\w.-]+ "
                          r"\$([\d.]+)", line)
            if m:
                iv["bot_cost"], iv["persona_cost"] = m.group(1), m.group(2)
        if "FAILED" in line:
            iv["status"] = "failed"

    return {"run_dir": run.name, "log": log_text[-40000:],
            "generator": generator, "pipeline": pipeline,
            "run_finished": run_finished,
            "started_at": started_at, "last_activity": last_activity,
            "judges": sorted(judges.values(), key=lambda j: j["index"]),
            "interviews": sorted(interviews.values(), key=lambda i: i["index"])}


def _model_name(raw):
    if not raw:
        return None
    name = raw.replace("claude-", "").replace("-", " ").strip()
    return ("Claude " + name.title()) if name else None


def _stage(num, sid, name, desc, usage):
    return {"id": sid, "number": num, "name": name, "description": desc,
            "status": "pending", "model": None, "usage": usage,
            "inputTokens": 0, "outputTokens": 0, "cost": 0.0,
            "progress": None, "latestActivity": None, "logs": []}


def build_lanes(d: dict) -> list[dict]:
    """One lane per persona: its own Interviewing -> Judge path."""
    lanes = []
    g = d.get("generator") or {}
    n = max(1, len(d["interviews"]))
    for iv in d["interviews"]:
        idx = iv["index"]
        j = next((x for x in d["judges"] if x["index"] == idx), None)

        # per-lane share of the batched generator call (1 call for all personas)
        pst = _stage(1, f"persona-{idx}", "Persona",
                     f"Batched generation (1/{n} share)", "Claude API")
        pst["status"] = "completed" if g.get("done") else "running"
        pst["model"] = _model_name(g.get("model"))
        if g.get("io"):
            pst["inputTokens"] = _num(g["io"]["in_tok"]) // n
            pst["outputTokens"] = _num(g["io"]["out_tok"]) // n
            pst["cost"] = _usd(g["io"]["total"]) / n
        pst["latestActivity"] = f"Generated in one batch call with {n} persona(s)"

        ist = _stage(2, f"interview-{idx}", "Interviewing",
                     iv["industry"], "Claude API")
        ist["status"] = ("running" if iv["status"] == "interviewing"
                         else "failed" if iv["status"] == "failed" else "completed")
        ist["model"] = _model_name(iv.get("bot_model"))
        if iv.get("io"):
            ist["inputTokens"] = _num(iv["io"]["in_tok"])
            ist["outputTokens"] = _num(iv["io"]["out_tok"])
            ist["cost"] = _usd(iv["io"]["total"])
        ist["progress"] = {"label": "turn", "value": iv["turn"]}
        ist["botCost"] = iv.get("bot_cost")
        ist["personaCost"] = iv.get("persona_cost")
        ist["latestActivity"] = (iv["log"][-1][:90] if iv["log"] else None)
        ist["logs"] = iv["log"][-40:]

        jst = _stage(3, f"judge-{idx}", "Judge",
                     "Evaluates requirements quality", "Claude API")
        if j:
            jst["status"] = "completed" if j.get("done") else "running"
            jst["model"] = _model_name(j.get("model"))
            if j.get("io"):
                jst["inputTokens"] = _num(j["io"]["in_tok"])
                jst["outputTokens"] = _num(j["io"]["out_tok"])
                jst["cost"] = _usd(j["io"]["total"])
            if j.get("done") and iv.get("score"):
                jst["latestActivity"] = (f"score {iv['score']}, "
                                         f"{iv['findings']} findings")
            elif jst["status"] == "running":
                jst["latestActivity"] = "Evaluating requirements…"
        elif ist["status"] == "running":
            jst["status"] = "queued"

        # per-lane share of the shared run-level Improver / Fixing Code steps
        shared = []
        for num, key, name, usage, run_note in (
                (4, "improve", "Improver", "Claude API", "Refining prompt…"),
                (5, "codefix", "Fixing Code", "Claude Membership",
                 "Applying code fixes…")):
            raw = d["pipeline"][key]
            st = _stage(num, f"{key}-{idx}", name,
                        f"Shared step (1/{n} share)", usage)
            st["status"] = {"run": "running", "done": "completed",
                            "failed": "failed"}.get(raw["status"], "pending")
            st["model"] = _model_name(raw.get("model"))
            if raw.get("io"):
                st["inputTokens"] = _num(raw["io"]["in_tok"]) // n
                st["outputTokens"] = _num(raw["io"]["out_tok"]) // n
                st["cost"] = _usd(raw["io"]["total"]) / n
            st["latestActivity"] = raw.get("note") or (
                run_note if st["status"] == "running" else None)
            shared.append(st)
        imp, fix = shared
        if jst["status"] == "completed" and imp["status"] == "pending":
            imp["status"] = "queued"
        if imp["status"] == "completed" and fix["status"] == "pending":
            fix["status"] = "queued"
        if fix["status"] == "completed":
            fix["name"] = "Completed Fixing Cycle"
            fix["latestActivity"] = (fix["latestActivity"]
                                     or "Cycle completed successfully.")

        lanes.append({"index": idx, "industry": iv["industry"],
                      "score": iv.get("score"),
                      "persona": pst, "interview": ist, "judge": jst,
                      "improve": imp, "codefix": fix})
    return lanes


def build_stages(d: dict) -> list[dict]:
    """Aggregate parsed run data into the 5 reusable stage objects."""
    interviews, judges = d["interviews"], d["judges"]
    g = d.get("generator") or {}
    p = d["pipeline"]
    stage = _stage

    s1 = stage(1, "persona", "Persona Generator",
               "Generates business-owner personas", "Claude API")
    s2 = stage(2, "interview", "Interviewing",
               "Bot interviews each persona", "Claude API")
    s3 = stage(3, "judge", "Judge", "Evaluates requirements quality", "Claude API")
    s4 = stage(4, "improve", "Improver", "Refines the interviewer prompt", "Claude API")
    s5 = stage(5, "codefix", "Fixing Code",
               "Applies code fixes for this cycle", "Claude Membership")

    # 1 — persona generator
    if g.get("model") or g.get("done") or interviews:
        s1["status"] = "completed" if g.get("done") else "running"
    s1["model"] = _model_name(g.get("model"))
    if g.get("io"):
        io = g["io"]
        s1["inputTokens"] = _num(io["in_tok"]); s1["outputTokens"] = _num(io["out_tok"])
        s1["cost"] = _usd(io["total"])
        s1["latestActivity"] = "Personas generated"
    elif s1["status"] == "running":
        s1["latestActivity"] = "Generating personas…"

    # 2 — interviewing (aggregate all interviews)
    if interviews:
        active = [iv for iv in interviews if iv["status"] == "interviewing"]
        s2["status"] = "running" if active else "completed"
        if any(iv["status"] == "failed" for iv in interviews):
            s2["status"] = "failed" if not active else "running"
        s2["model"] = _model_name(interviews[0].get("bot_model"))
        for iv in interviews:
            if iv.get("io"):
                s2["inputTokens"] += _num(iv["io"]["in_tok"])
                s2["outputTokens"] += _num(iv["io"]["out_tok"])
                s2["cost"] += _usd(iv["io"]["total"])
            if iv["log"]:
                s2["logs"] += [f"#{iv['index']} {l}" for l in iv["log"][-4:]]
        cur = active[0] if active else interviews[-1]
        s2["progress"] = {"label": "turn", "value": cur["turn"]}
        s2["latestActivity"] = (f"#{cur['index']} {cur['industry']} — turn {cur['turn']}"
                                if active else
                                f"{len(interviews)} interview(s) finished")
        s2["logs"] = s2["logs"][-40:]

    # 3 — judge
    if judges:
        s3["status"] = "completed" if all(j.get("done") for j in judges) else "running"
        s3["model"] = _model_name(judges[-1].get("model"))
        for j in judges:
            if j.get("io"):
                s3["inputTokens"] += _num(j["io"]["in_tok"])
                s3["outputTokens"] += _num(j["io"]["out_tok"])
                s3["cost"] += _usd(j["io"]["total"])
        scored = [iv for iv in interviews if iv.get("score")]
        if s3["status"] == "completed" and scored:
            s3["latestActivity"] = ", ".join(
                f"#{iv['index']} score {iv['score']}" for iv in scored)[:80]
        elif s3["status"] == "running":
            s3["latestActivity"] = "Evaluating requirements…"
    elif s2["status"] == "running":
        s3["status"] = "queued"

    # 4 — improver / 5 — codefix (from the shared pipeline stages)
    for st, raw, run_note in ((s4, p["improve"], "Refining prompt…"),
                              (s5, p["codefix"], "Applying code fixes…")):
        status = raw["status"]
        st["status"] = {"run": "running", "done": "completed",
                        "failed": "failed"}.get(status, "pending")
        st["model"] = _model_name(raw.get("model"))
        if raw.get("io"):
            st["inputTokens"] = _num(raw["io"]["in_tok"])
            st["outputTokens"] = _num(raw["io"]["out_tok"])
            st["cost"] = _usd(raw["io"]["total"])
        st["latestActivity"] = raw.get("note") or (
            run_note if st["status"] == "running" else None)
    if s3["status"] == "completed" and s4["status"] == "pending":
        s4["status"] = "queued"
    if s4["status"] == "completed" and s5["status"] == "pending":
        s5["status"] = "queued"
    if s5["status"] == "completed":
        s5["name"] = "Completed Fixing Cycle"
        s5["latestActivity"] = s5["latestActivity"] or "Cycle completed successfully."

    return [s1, s2, s3, s4, s5]


def payload() -> dict:
    d = collect()
    stages = build_stages(d) if d["run_dir"] else []
    completed = sum(1 for s in stages if s["status"] == "completed")
    failed = any(s["status"] == "failed" for s in stages)
    running = [s for s in stages if s["status"] == "running"]
    if failed:
        overall = "failed"
    elif stages and completed == len(stages):
        overall = "completed"
    elif d["run_finished"] and not running:
        overall = "completed"
    elif running or completed:
        overall = "running"
    else:
        overall = "idle"
    now = time.time()
    started = d.get("started_at")
    end = d.get("last_activity") if overall in ("completed", "failed") else now
    return {
        "run_id": d["run_dir"], "log": d["log"],
        "stages": stages, "lanes": build_lanes(d) if stages else [],
        "overall": overall,
        "completed_stages": completed, "total_stages": len(stages) or 5,
        "current_stage": (running[0]["name"] if running else
                          ("—" if not stages or overall != "running"
                           else next((s["name"] for s in stages
                                      if s["status"] in ("queued", "pending")),
                                     stages[-1]["name"]))),
        "fix_cycle_done": bool(stages) and stages[-1]["status"] == "completed",
        "elapsed": max(0, int((end or now) - started)) if started else None,
        "started_at": started,
        "last_activity_ago": (max(0, int(now - d["last_activity"]))
                              if d.get("last_activity") else None),
        "total_cost": round(sum(s["cost"] for s in stages), 2),
        "total_in": sum(s["inputTokens"] for s in stages),
        "total_out": sum(s["outputTokens"] for s in stages),
    }


PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RequirementsBot — Cycle Monitor</title>
<style>
:root{
  --bg:#0d0d0d; --panel:#151515; --panel2:#1a1a1a; --border:#2a2a2a;
  --border-hi:#3f3f46; --text:#f5f5f5; --text2:#a1a1aa; --muted:#71717a;
  --accent:#6ea8fe; --green:#4ade80; --red:#f87171; --amber:#fbbf24;
  --mono:'Cascadia Code',Consolas,'SF Mono',monospace;
  --sans:-apple-system,'Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 var(--sans)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:#333;border-radius:4px}
::-webkit-scrollbar-track{background:transparent}

/* ---------- app shell ---------- */
#shell{display:flex;height:100vh;overflow:hidden}
#sidebar{width:190px;flex:none;background:var(--panel);border-right:1px solid var(--border);
  display:flex;flex-direction:column;transition:width .15s ease;overflow:hidden}
#sidebar.collapsed{width:44px}
#sb-head{display:flex;align-items:center;gap:8px;padding:12px 12px;border-bottom:1px solid var(--border)}
#sb-logo{width:20px;height:20px;flex:none;border:1px solid var(--border-hi);border-radius:5px;
  display:grid;place-items:center;font:600 10px var(--mono);color:var(--accent)}
#sb-title{font-weight:600;font-size:13px;white-space:nowrap}
#sb-toggle{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:13px;padding:2px 4px;border-radius:4px}
#sb-toggle:hover{color:var(--text);background:var(--panel2)}
#sb-nav{padding:8px 6px;flex:1;overflow-y:auto}
.nav-item{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:5px;
  color:var(--text2);cursor:pointer;white-space:nowrap;font-size:12.5px}
.nav-item:hover{background:var(--panel2);color:var(--text)}
.nav-item.active{background:var(--panel2);color:var(--text)}
.nav-item.active .nav-ico{color:var(--accent)}
.nav-ico{width:16px;text-align:center;flex:none;font-size:12px;color:var(--muted)}
#sb-foot{border-top:1px solid var(--border);padding:10px 12px;font:11px var(--mono);
  color:var(--muted);white-space:nowrap}
#sb-foot .dot{display:inline-block;width:6px;height:6px;border-radius:50%;
  background:var(--green);margin-right:6px;vertical-align:1px}
#sb-foot div{margin:3px 0}
#sidebar.collapsed #sb-title,#sidebar.collapsed .nav-label,#sidebar.collapsed #sb-foot{display:none}

#main{flex:1;display:flex;flex-direction:column;min-width:0;overflow-y:auto}

/* ---------- run header ---------- */
#run-header{display:flex;align-items:center;gap:14px;padding:12px 20px;
  border-bottom:1px solid var(--border);background:var(--panel);position:sticky;top:0;z-index:5}
#run-title{font-size:14px;font-weight:600}
#run-id{font:12px var(--mono);color:var(--text2)}
#run-meta{color:var(--muted);font-size:12px}
#run-actions{margin-left:auto;display:flex;gap:6px}
.act{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer}
.act:hover{border-color:var(--border-hi);color:var(--text)}

/* ---------- status badges ---------- */
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:99px;
  font:600 11px var(--sans);border:1px solid var(--border)}
.badge .b-dot{width:6px;height:6px;border-radius:50%;flex:none}
.badge.running{color:var(--accent);border-color:#28405f;background:rgba(110,168,254,.07)}
.badge.running .b-dot{background:var(--accent);animation:pulse 1.6s ease-in-out infinite}
.badge.completed{color:var(--green);border-color:#234534;background:rgba(74,222,128,.06)}
.badge.completed .b-dot{background:var(--green)}
.badge.failed{color:var(--red);border-color:#552b2b;background:rgba(248,113,113,.06)}
.badge.failed .b-dot{background:var(--red)}
.badge.pending,.badge.queued,.badge.idle{color:var(--muted)}
.badge.pending .b-dot,.badge.queued .b-dot,.badge.idle .b-dot{background:var(--muted)}
.badge.retrying{color:var(--amber);border-color:#5c4a1e}
.badge.retrying .b-dot{background:var(--amber)}
.badge.completed_failures{color:var(--amber);border-color:#5c4a1e;background:rgba(251,191,36,.05)}
.badge.completed_failures .b-dot{background:var(--amber)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ---------- summary toolbar ---------- */
#summary{display:grid;width:auto;margin:14px 20px 0;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;
  grid-template-columns:minmax(95px,.85fr) minmax(110px,1fr) minmax(110px,1fr)
    minmax(105px,.95fr) minmax(130px,1.15fr) minmax(110px,1fr)
    minmax(105px,1fr) minmax(125px,1.1fr)}
.sum{padding:8px 14px;min-width:0;position:relative;
  display:flex;flex-direction:column;justify-content:center}
.sum::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.sum:last-child::after{display:none}
.sum .k{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sum .v{font:500 14px var(--mono);color:var(--text);margin-top:1px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
#s-running.hot{color:var(--accent)}
#s-completed-c{color:var(--green)}
#s-failed.hot{color:var(--red)}
@media (max-width:1180px){
  #summary{grid-template-columns:repeat(4,1fr)}
  .sum:nth-child(4)::after{display:none}
  .sum:nth-child(-n+4){border-bottom:1px solid var(--border)}
}

/* ---------- pipeline ---------- */
#improve-grid{display:flex;gap:14px;margin:14px 20px 0;flex-wrap:wrap}
.imp-tile{width:190px;aspect-ratio:1;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;cursor:pointer;color:var(--text);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:10px;padding:14px;text-align:center;transition:border-color .15s,background .15s}
.imp-tile:hover{border-color:var(--border-hi);background:var(--panel2)}
.imp-tile.on{border-color:var(--accent)}
.imp-tile .bx-ico{width:38px;height:38px;border:1px solid var(--border-hi);
  border-radius:8px;display:grid;place-items:center;font-size:18px;color:var(--accent)}
.imp-tile .bx-t{font:600 12.5px var(--sans);line-height:1.35}
.imp-tile .bx-d{font:10px var(--mono);color:var(--muted);line-height:1.4}
.imp-panel.closed{display:none}
.imp-panel.plc{margin:14px 20px 0;background:var(--panel);border:1px solid var(--border);
  border-radius:6px}
.empty{padding:40px 24px;text-align:center;color:var(--muted);font-size:12.5px;
  line-height:1.6;max-width:640px;margin:0 auto}
body.view-logs #improve-grid,body.view-chat #improve-grid,
body.view-logs .imp-panel,body.view-chat .imp-panel{display:none}
#pipeline-wrap{padding:14px 20px 0;overflow-x:auto}
#pipeline{display:flex;align-items:stretch;min-width:940px}
.stage{flex:1;min-width:172px;background:var(--panel);border:1px solid var(--border);
  border-radius:6px;padding:11px 12px;cursor:pointer;transition:border-color .2s,background .2s;
  display:flex;flex-direction:column;gap:7px}
.stage:hover{border-color:var(--border-hi)}
.stage.running{border-color:#3b5b8a;background:#161a20}
.stage.completed{border-color:#2a3a30}
.stage.failed{border-color:#553030}
.stage.pending,.stage.queued{opacity:.62}
.stage.selected{border-color:var(--accent)}
.st-top{display:flex;align-items:center;gap:8px}
.st-num{font:600 10px var(--mono);color:var(--muted);border:1px solid var(--border);
  border-radius:4px;width:18px;height:18px;display:grid;place-items:center;flex:none}
.st-ico{font-size:13px;flex:none;color:var(--text2)}
.st-name{font-weight:600;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.st-desc{color:var(--muted);font-size:11px;margin-top:-4px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.st-status{display:flex;align-items:center;gap:8px;font-size:11px}
.st-status .turn{font:11px var(--mono);color:var(--text2)}
.spin{display:inline-block;width:10px;height:10px;border:1.5px solid var(--border-hi);
  border-top-color:var(--accent);border-radius:50%;animation:rot .8s linear infinite;flex:none}
@keyframes rot{to{transform:rotate(360deg)}}
.st-bar{height:2px;background:var(--border);border-radius:1px;overflow:hidden}
.st-bar i{display:block;height:100%;width:100%}
.stage.completed .st-bar i{background:var(--green);opacity:.55}
.stage.failed .st-bar i{background:var(--red);opacity:.6}
.stage.running .st-bar i{background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:flow 1.4s linear infinite}
.stage.pending .st-bar i,.stage.queued .st-bar i{background:transparent}
@keyframes flow{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
.st-metrics{display:flex;flex-direction:column;gap:2px;font:11px var(--mono)}
.st-metrics span{color:var(--muted);font-size:9.5px;text-transform:uppercase;
  letter-spacing:.04em;display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.st-metrics b{color:var(--text);font-weight:500;font-size:11.5px}
.st-model{font:10.5px var(--mono);color:var(--text2);border-top:1px solid var(--border);
  padding-top:6px;line-height:1.6}
.st-model .lbl{color:var(--muted)}

#lanes{flex:2;display:flex;flex-direction:column;gap:18px;min-width:0}
.lane-bar{display:grid;align-items:stretch;background:var(--panel);
  border:1px solid var(--border);border-radius:6px;margin-bottom:8px;
  min-height:44px;
  grid-template-columns:minmax(260px,1.5fr) minmax(70px,.45fr) minmax(135px,.85fr)
    minmax(80px,.5fr) minmax(250px,1.6fr) minmax(105px,.65fr)
    minmax(110px,.7fr) 28px}
.lane-bar.failed{border-color:#553030}
.lane-name{display:flex;align-items:baseline;gap:10px;padding:4px 14px;min-width:0;
  position:relative}
.lane-name::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.lane-name .n{font:600 12px var(--sans);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;min-width:0}
.lane-name .s{font:10px var(--mono);color:var(--muted);white-space:nowrap;flex:none}
.lb-item{display:flex;flex-direction:column;justify-content:center;min-width:0;
  padding:4px 12px;position:relative}
.lb-item::after{content:'';position:absolute;right:0;top:8px;bottom:8px;width:1px;
  background:var(--border)}
.lb-item:last-of-type::after{display:none}
.lb-item .k{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lb-item .v{font:500 11.5px var(--mono);color:var(--text);white-space:nowrap;margin-top:1px;
  overflow:hidden;text-overflow:ellipsis}
.lb-item .v.err{color:var(--red)}
.lb-more{background:none;border:none;color:var(--muted);cursor:pointer;
  font-size:14px;padding:0;align-self:center;justify-self:center}
.lb-more:hover{color:var(--text)}
@media (max-width:1240px){
  .lane-bar{grid-template-columns:minmax(220px,1.4fr) minmax(70px,.45fr)
    minmax(110px,.75fr) minmax(75px,.5fr) minmax(200px,1.25fr)
    minmax(100px,.65fr) 28px}
  .lb-item.last-act{display:none}
}
.lane-row{display:flex;align-items:stretch}
.lane-row .stage{min-width:150px}
.connector{flex:none;width:26px;display:flex;align-items:center;position:relative}
.connector::before{content:'';height:1px;width:100%;background:var(--border)}
.connector.completed::before{background:#2f5c40}
.connector.running::before{background:linear-gradient(90deg,#2f5c40,var(--accent))}
.connector.running::after{content:'';position:absolute;width:4px;height:4px;border-radius:50%;
  background:var(--accent);top:50%;margin-top:-2px;animation:travel 1.2s linear infinite}
@keyframes travel{from{left:0;opacity:0}20%{opacity:1}80%{opacity:1}to{left:calc(100% - 4px);opacity:0}}

/* ---------- console ---------- */
#console{margin:14px 20px 20px;background:#101010;border:1px solid var(--border);
  border-radius:6px;display:flex;flex-direction:column;min-height:220px;flex:1}
#console.fullscreen{position:fixed;inset:12px;z-index:50;margin:0}
#con-bar{display:flex;align-items:center;gap:6px;padding:7px 10px;
  border-bottom:1px solid var(--border);flex-wrap:wrap}
#con-title{font:600 11px var(--sans);text-transform:uppercase;letter-spacing:.07em;
  color:var(--text2);margin-right:6px}
.chip{background:none;border:1px solid var(--border);color:var(--muted);border-radius:99px;
  padding:2px 9px;font-size:11px;cursor:pointer}
.chip:hover{color:var(--text2);border-color:var(--border-hi)}
.chip.on{color:var(--text);border-color:var(--border-hi);background:var(--panel2)}
#con-search{margin-left:auto;background:var(--panel2);border:1px solid var(--border);
  color:var(--text);border-radius:5px;padding:3px 9px;font:11.5px var(--mono);width:150px}
#con-search:focus{outline:none;border-color:var(--border-hi)}
.con-btn{background:none;border:1px solid var(--border);color:var(--muted);border-radius:5px;
  padding:3px 8px;font-size:11px;cursor:pointer}
.con-btn:hover{color:var(--text);border-color:var(--border-hi)}
.con-btn.on{color:var(--accent);border-color:#28405f}
#con-body{flex:1;overflow-y:auto;padding:8px 12px;font:11.5px/1.65 var(--mono);min-height:120px}
.ll{white-space:pre-wrap;word-break:break-all}
.ll .tag{display:inline-block;min-width:74px;color:var(--muted)}
.ll.persona .tag{color:#c084fc}.ll.interview .tag{color:var(--accent)}
.ll.judge .tag{color:var(--amber)}.ll.improve .tag{color:#67e8f9}
.ll.code .tag{color:var(--green)}.ll.system .tag{color:var(--muted)}
.ll.error{color:var(--red)}.ll.error .tag{color:var(--red)}
.ll .body{color:#c7cbd1}

/* ---------- inspector ---------- */
#inspector{position:fixed;top:0;right:-380px;width:360px;height:100vh;background:var(--panel);
  border-left:1px solid var(--border);z-index:40;transition:right .18s ease;
  display:flex;flex-direction:column}
#inspector.open{right:0;box-shadow:-18px 0 40px rgba(0,0,0,.45)}
#insp-head{display:flex;align-items:center;gap:10px;padding:14px 16px;
  border-bottom:1px solid var(--border)}
#insp-title{font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.05em}
#insp-close{margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:15px}
#insp-close:hover{color:var(--text)}
#insp-body{flex:1;overflow-y:auto;padding:14px 16px}
.insp-sec{margin-bottom:16px}
.insp-sec h4{margin:0 0 7px;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:600}
.kv{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}
.kv .k{color:var(--text2)}.kv .v{font-family:var(--mono);font-size:11.5px}
#insp-logs{background:#101010;border:1px solid var(--border);border-radius:5px;
  padding:8px 10px;font:11px/1.6 var(--mono);color:#c7cbd1;max-height:240px;
  overflow-y:auto;white-space:pre-wrap;word-break:break-all}

#empty{padding:60px 20px;text-align:center;color:var(--muted)}

/* ---------- views: Cycles (pipeline) / Live Logs (console) / Chat ---------- */
body.view-cycles #console{display:none}
body.view-logs #summary,body.view-logs #pipeline-wrap{display:none}
body.view-logs #console{flex:1}
#chat{display:none}
body.view-chat #summary,body.view-chat #pipeline-wrap,body.view-chat #console{display:none}
body.view-chat #chat{display:flex}

/* ---------- chat ---------- */
#chat{flex:1;flex-direction:column;margin:14px 20px 20px;min-height:0;
  background:var(--panel);border:1px solid var(--border);border-radius:6px}
#chat-head{display:flex;align-items:center;gap:10px;padding:9px 14px;
  border-bottom:1px solid var(--border)}
#chat-head .t{font:600 11px var(--sans);text-transform:uppercase;
  letter-spacing:.07em;color:var(--text2)}
#chat-head .m{font:10.5px var(--mono);color:var(--muted)}
#chat-reset{margin-left:auto}
#chat-thread{flex:1;overflow-y:auto;padding:16px 18px;display:flex;
  flex-direction:column;gap:12px;min-height:0}
.msg{max-width:72%;border:1px solid var(--border);border-radius:6px;
  padding:8px 12px;font-size:13px;line-height:1.55;white-space:pre-wrap;
  overflow-wrap:break-word}
.msg .who{font:600 9.5px var(--mono);text-transform:uppercase;
  letter-spacing:.07em;color:var(--muted);margin-bottom:3px}
.msg.ai{align-self:flex-start;background:var(--panel2)}
.msg.human{align-self:flex-end;background:#161a20;border-color:#2b3a52}
.msg.err{align-self:stretch;max-width:none;border-color:#553030;color:var(--red);
  font-family:var(--mono);font-size:11.5px}
.msg.sys{align-self:center;max-width:none;border:none;background:none;
  color:var(--green);font:11px var(--mono)}
#chat-typing{align-self:flex-start;color:var(--muted);font:11.5px var(--mono);
  padding:2px 4px}
#chat-typing .spin{margin-right:6px;vertical-align:-1px}
#chat-bar{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border);
  align-items:flex-end}
#chat-input{flex:1;background:var(--panel2);border:1px solid var(--border);
  color:var(--text);border-radius:6px;padding:8px 12px;font:13px/1.5 var(--sans);
  resize:none;min-height:38px;max-height:140px}
#chat-input:focus{outline:none;border-color:var(--border-hi)}
#chat-send{background:#1d2a3f;border:1px solid #2b3a52;color:var(--text);
  border-radius:6px;padding:8px 16px;font:600 12px var(--sans);cursor:pointer}
#chat-send:hover{border-color:var(--accent)}
#chat-send:disabled{opacity:.5;cursor:default}
#chat-attach{background:var(--panel2);border:1px solid var(--border);color:var(--text2);
  border-radius:6px;padding:8px 11px;font-size:13px;cursor:pointer}
#chat-attach:hover{border-color:var(--border-hi);color:var(--text)}
#chat-thread.drop{outline:1px dashed var(--accent);outline-offset:-6px}
@media (max-width:760px){
  #sidebar{display:none}
  #pipeline{flex-direction:column;min-width:0}
  .connector{width:auto;height:20px;justify-content:center;margin-left:20px}
  .connector::before{width:1px;height:100%}
}
</style></head><body class="view-cycles">
<div id="shell">
  <aside id="sidebar">
    <div id="sb-head"><div id="sb-logo">R</div><span id="sb-title">RequirementsBot</span>
      <button id="sb-toggle" title="Collapse">⟨⟩</button></div>
    <nav id="sb-nav">
      <div class="nav-item" id="nav-chat" data-view="chat"><span class="nav-ico">▶</span><span class="nav-label">Requirement Bot Chat</span></div>
      <div class="nav-item" id="nav-logs" data-view="logs"><span class="nav-ico">≣</span><span class="nav-label">Live Logs</span></div>
      <div class="nav-item active" id="nav-cycles" data-view="cycles"><span class="nav-ico">◈</span><span class="nav-label">Learning &amp; Training Cycles</span></div>
      <div class="nav-item"><span class="nav-ico">◉</span><span class="nav-label">Personas</span></div>
      <div class="nav-item"><span class="nav-ico">✎</span><span class="nav-label">Interviews</span></div>
      <div class="nav-item"><span class="nav-ico">⚖</span><span class="nav-label">Evaluations</span></div>
      <div class="nav-item"><span class="nav-ico">‹›</span><span class="nav-label">Fixes</span></div>
      <div class="nav-item"><span class="nav-ico">≡</span><span class="nav-label">Reports</span></div>
      <div class="nav-item"><span class="nav-ico">⚙</span><span class="nav-label">Settings</span></div>
    </nav>
    <div id="sb-foot">
      <div><span class="dot"></span>API connected</div>
      <div><span class="dot"></span>Claude operational</div>
      <div style="color:#52525b">v1.0.0</div>
    </div>
  </aside>

  <div id="main">
    <div id="run-header">
      <div>
        <div id="run-title">Cycle / Run Detail</div>
        <span id="run-id">—</span> <span id="run-meta"></span>
      </div>
      <span class="badge idle" id="run-badge"><span class="b-dot"></span><span id="run-badge-txt">Idle</span></span>
      <div id="run-actions">
        <button class="act" title="Pause">⏸</button>
        <button class="act" title="Stop">■</button>
        <button class="act" title="Restart">↻</button>
        <button class="act" title="Settings">⚙</button>
        <button class="act" title="More">⋯</button>
      </div>
    </div>

    <div id="summary">
      <div class="sum"><div class="k">Total Cost</div><div class="v" id="s-cost">$0.00</div></div>
      <div class="sum"><div class="k">Input Tokens</div><div class="v" id="s-in">0</div></div>
      <div class="sum"><div class="k">Output Tokens</div><div class="v" id="s-out">0</div></div>
      <div class="sum"><div class="k">Elapsed Time</div><div class="v" id="s-elapsed">—</div></div>
      <div class="sum"><div class="k">Run State</div>
        <div class="v" style="margin-top:1px"><span class="badge idle" id="s-state"><span class="b-dot"></span><span id="s-state-txt">Idle</span></span></div></div>
      <div class="sum"><div class="k">Cycles Running</div><div class="v" id="s-running">0</div></div>
      <div class="sum"><div class="k">Failed Cycles</div><div class="v" id="s-failed">0</div></div>
      <div class="sum"><div class="k">Completed Cycles</div><div class="v" id="s-completed-c">0</div></div>
    </div>

    <div id="improve-grid">
      <button class="imp-tile" data-panel="pipeline-wrap">
        <span class="bx-ico">⟳</span>
        <span class="bx-t">Requirement Bot Improvement Cycle</span>
        <span class="bx-d">persona interviews, judging, prompt improvement and code fixes</span>
      </button>
      <button class="imp-tile" data-panel="builder-wrap">
        <span class="bx-ico">🛠</span>
        <span class="bx-t">Builder Bot Improvement Cycle</span>
        <span class="bx-d">builds the chatbot from the brief, then improves itself</span>
      </button>
      <button class="imp-tile" data-panel="brief-wrap">
        <span class="bx-ico">📋</span>
        <span class="bx-t">Brief Quality Cycle</span>
        <span class="bx-d">a builder persona tries to build from each brief and reports what is ambiguous or missing</span>
      </button>
      <button class="imp-tile" data-panel="endcust-wrap">
        <span class="bx-ico">🎭</span>
        <span class="bx-t">End-Customer Simulation Cycle</span>
        <span class="bx-d">personas play customers of the built chatbot; a judge scores how it handled them</span>
      </button>
      <button class="imp-tile" data-panel="feedback-wrap">
        <span class="bx-ico">📥</span>
        <span class="bx-t">Live Feedback Cycle</span>
        <span class="bx-d">replays real conversation transcripts as test cases; real failures beat synthetic ones</span>
      </button>
      <button class="imp-tile" data-panel="regression-wrap">
        <span class="bx-ico">🛡</span>
        <span class="bx-t">Regression Cycle</span>
        <span class="bx-d">re-runs golden interviews after every change and diffs scores, so improvements never quietly break things</span>
      </button>
    </div>
    <div id="pipeline-wrap" class="closed imp-panel"><div id="pipeline"><div id="empty">Waiting for a run to appear in parallel_runs/ …</div></div></div>
    <div id="builder-wrap" class="closed imp-panel plc"><div class="empty">No Builder Bot runs yet — this cycle is not built yet. It will build the chatbot from each brief, judge the result against the brief, then improve its own prompt and code.</div></div>
    <div id="brief-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. This cycle scores the BRIEF itself, not the interview: a builder persona tries to build from it and files everything ambiguous, missing, or contradictory back to the improver.</div></div>
    <div id="endcust-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. The ultimate test: personas play customers of the BUILT chatbot (bookings, complaints, discounts) and a judge scores whether it handled them like the brief promised.</div></div>
    <div id="feedback-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. Same machinery as the persona runs, but the test cases are REAL transcripts and uploaded chat exports instead of generated personas.</div></div>
    <div id="regression-wrap" class="closed imp-panel plc"><div class="empty">Not built yet. Cheapest and most protective: a fixed set of golden interviews re-runs after every prompt or code change, and score diffs catch an "improvement" that quietly makes things worse.</div></div>

    <div id="console">
      <div id="con-bar">
        <span id="con-title">Live Cycle Logs</span>
        <button class="chip on" data-f="all">All</button>
        <button class="chip" data-f="persona">Persona</button>
        <button class="chip" data-f="interview">Interview</button>
        <button class="chip" data-f="judge">Judge</button>
        <button class="chip" data-f="improve">Improver</button>
        <button class="chip" data-f="code">Fixing Code</button>
        <button class="chip" data-f="system">System</button>
        <button class="chip" data-f="error">Errors</button>
        <input id="con-search" placeholder="filter…" spellcheck="false">
        <button class="con-btn on" id="con-scroll" title="Auto-scroll">⇣ auto</button>
        <button class="con-btn" id="con-pause" title="Pause updates">⏸</button>
        <button class="con-btn" id="con-copy" title="Copy logs">⧉</button>
        <button class="con-btn" id="con-clear" title="Clear view">✕</button>
        <button class="con-btn" id="con-full" title="Fullscreen">⛶</button>
      </div>
      <div id="con-body"></div>
    </div>

    <div id="chat">
      <div id="chat-head"><span class="t">Requirement Bot Chat</span>
        <span class="m">interactive interview · put files in uploads/ when asked</span>
        <button class="act" id="chat-reset" title="Start a new interview">↺ New interview</button></div>
      <div id="chat-thread"></div>
      <div id="chat-bar">
        <input type="file" id="chat-file" multiple hidden
               accept=".txt,.md,.csv,.png,.jpg,.jpeg,.webp,.gif">
        <button id="chat-attach" title="Attach chat exports / screenshots (saved to uploads/)">📎</button>
        <textarea id="chat-input" rows="1" placeholder="Type your answer… (Enter to send, Shift+Enter for newline)" spellcheck="false"></textarea>
        <button id="chat-send">Send</button>
      </div>
    </div>
  </div>

  <div id="inspector">
    <div id="insp-head"><span id="insp-title">Stage</span>
      <span class="badge idle" id="insp-badge"><span class="b-dot"></span><span id="insp-badge-txt"></span></span>
      <button id="insp-close">✕</button></div>
    <div id="insp-body"></div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const STAGE_ICONS = {persona:'◉', interview:'✎', judge:'⚖', improve:'⟳', codefix:'‹›'};
const STATUS_TXT = {pending:'Pending', queued:'Queued', running:'Running',
                    completed:'Completed', failed:'Failed', retrying:'Retrying', idle:'Idle',
                    completed_failures:'Completed With Failures'};
const STATUS_DOT = {pending:'○', queued:'○', running:'●', completed:'✓', failed:'✕'};
let state = {stages:[], lanes:[], selected:null, paused:false, autoscroll:true,
             filter:'all', search:'', fullscreen:false, cleared:0};

function fmtTok(n){
  if(!n) return '0';
  if(n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if(n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return String(n);
}
function fmtElapsed(s){
  if(s == null) return '—';
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60), sec = s%60;
  return (h ? h+'h ' : '') + (h||m ? m+'m ' : '') + sec+'s';
}
function badge(el, status){
  el.parentElement ? null : 0;
  el.className = 'badge ' + status;
}

/* ---------- stage cards ---------- */
function stageCard(s){
  const running = s.status === 'running';
  const statusRow = running
    ? `<span class="spin"></span><span style="color:var(--accent);font-weight:600">Running</span>` +
      (s.progress ? `<span class="turn">${s.progress.label} ${s.progress.value}</span>` : '')
    : `<span style="color:${s.status==='completed'?'var(--green)':s.status==='failed'?'var(--red)':'var(--muted)'}">
       ${STATUS_DOT[s.status]||'○'} ${STATUS_TXT[s.status]||s.status}</span>`;
  return `<div class="stage ${s.status}${state.selected===s.id?' selected':''}" data-id="${s.id}">
    <div class="st-top"><span class="st-num">${s.number}</span>
      <span class="st-ico">${STAGE_ICONS[s.id.split('-')[0]]||'▣'}</span>
      <span class="st-name">${s.name}</span></div>
    <div class="st-desc">${s.description}</div>
    <div class="st-status">${statusRow}</div>
    <div class="st-bar"><i></i></div>
    <div class="st-metrics">
      <span>Cost<b>$${s.cost.toFixed(2)}</b></span>
      <span>Input Tokens<b>${fmtTok(s.inputTokens)}</b></span>
      <span>Output Tokens<b>${fmtTok(s.outputTokens)}</b></span>
    </div>
    <div class="st-model">
      <span class="lbl">model</span> ${s.model||'—'}<br>
      <span class="lbl">usage</span> ${s.usage}
    </div>
  </div>`;
}
function connector(prev, next){
  let cls = 'pending';
  if(prev.status === 'completed') cls = (next.status === 'running') ? 'running' : 'completed';
  else if(prev.status === 'running') cls = 'running';
  return `<div class="connector ${cls}"></div>`;
}
function allStages(){
  const lane = state.lanes.flatMap(l =>
    [l.persona, l.interview, l.judge, l.improve, l.codefix]);
  return state.stages.concat(lane);
}
function renderPipeline(){
  const p = $('#pipeline');
  if(!state.stages.length){ return; }
  const [s1, s2, s3, s4, s5] = state.stages;
  let html;
  if(state.lanes.length){
    html = `<div id="lanes">` + state.lanes.map(l => {
      const seq = [l.persona, l.interview, l.judge, l.improve, l.codefix];
      const cost = seq.reduce((a,s) => a + s.cost, 0);
      const tin = seq.reduce((a,s) => a + s.inputTokens, 0);
      const tout = seq.reduce((a,s) => a + s.outputTokens, 0);
      const done = seq.filter(s => s.status === 'completed').length;
      const running = seq.find(s => s.status === 'running');
      const failedStage = seq.find(s => s.status === 'failed');
      const st = running ? 'running'
               : failedStage ? 'failed'
               : done === seq.length ? 'completed'
               : seq.some(s => s.status === 'queued') ? 'queued' : 'pending';
      // "Step 4 / 5 · Fixing Code" — the stage being worked on (or where it stopped)
      const cur = running || failedStage
                || (done === seq.length ? seq[seq.length-1] : seq[Math.min(done, seq.length-1)]);
      const stepNo = seq.indexOf(cur) + 1;
      const ago = state.lastActivityAgo;
      const lastAct = ago == null ? '—'
        : (st === 'failed' ? 'Failed ' : '') + fmtElapsed(ago) + ' ago';
      return `<div class="lane">
        <div class="lane-bar${st === 'failed' ? ' failed' : ''}">
          <div class="lane-name"><span class="n">#${l.index} ${l.industry}</span>
            ${l.score ? `<span class="s">score ${l.score}</span>` : ''}</div>
          <div class="lb-item"><span class="k">Cost</span><span class="v">$${cost.toFixed(2)}</span></div>
          <div class="lb-item"><span class="k">Tokens</span>
            <span class="v" title="Input ${tin.toLocaleString()}\nOutput ${tout.toLocaleString()}">${fmtTok(tin)} / ${fmtTok(tout)}</span></div>
          <div class="lb-item"><span class="k">Elapsed</span><span class="v">${fmtElapsed(state.elapsed)}</span></div>
          <div class="lb-item"><span class="k">Step</span>
            <span class="v">Step ${stepNo} / ${seq.length} · ${cur.name}</span></div>
          <div class="lb-item"><span class="k">State</span>
            <span class="v" style="margin-top:2px"><span class="badge ${st}"><span class="b-dot"></span>${STATUS_TXT[st]}</span></span></div>
          <div class="lb-item last-act"><span class="k">Last Activity</span>
            <span class="v${st === 'failed' ? ' err' : ''}">${lastAct}</span></div>
          <button class="lb-more" title="More">⋯</button>
        </div>
        <div class="lane-row">${seq.map((s,i) =>
          (i ? connector(seq[i-1], s) : '') + stageCard(s)).join('')}</div>
      </div>`;
    }).join('') + `</div>`;
  }else{
    html = stageCard(s1) + connector(s1, s2) + stageCard(s2)
         + connector(s2, s3) + stageCard(s3) + connector(s3, s4)
         + stageCard(s4) + connector(s4, s5) + stageCard(s5);
  }
  p.innerHTML = html;
  p.querySelectorAll('.stage').forEach(el =>
    el.onclick = () => openInspector(el.dataset.id));
}

/* ---------- inspector ---------- */
function openInspector(id){
  state.selected = id; renderPipeline();
  const s = allStages().find(x => x.id === id); if(!s) return;
  $('#insp-title').textContent = s.name;
  $('#insp-badge').className = 'badge ' + s.status;
  $('#insp-badge-txt').textContent = STATUS_TXT[s.status] || s.status;
  const kv = (k,v) => `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $('#insp-body').innerHTML = `
    <div class="insp-sec"><h4>Overview</h4>
      ${kv('Status', STATUS_TXT[s.status]||s.status)}
      ${kv('Model', s.model||'—')}
      ${kv('Usage', s.usage)}
      ${s.progress ? kv('Progress', s.progress.label+' '+s.progress.value) : ''}
    </div>
    <div class="insp-sec"><h4>Tokens</h4>
      ${kv('Input', s.inputTokens.toLocaleString())}
      ${kv('Output', s.outputTokens.toLocaleString())}
    </div>
    <div class="insp-sec"><h4>Cost</h4>
      ${kv('Total', '$'+s.cost.toFixed(2))}
      ${s.id.startsWith('interview') ? (
        s.botCost != null
          ? kv('RequirementsBot', '$'+s.botCost) + kv('Persona', '$'+s.personaCost)
          : kv('RequirementsBot / Persona split', 'available when interview ends')
      ) : ''}
    </div>
    <div class="insp-sec"><h4>Latest activity</h4>
      <div style="font-size:12px;color:var(--text2)">${s.latestActivity||'—'}</div></div>
    <div class="insp-sec"><h4>Logs</h4>
      <div id="insp-logs">${(s.logs&&s.logs.length)?s.logs.join('\n'):'(no stage logs)'}</div></div>`;
  $('#inspector').classList.add('open');
}
$('#insp-close').onclick = () => {
  $('#inspector').classList.remove('open'); state.selected = null; renderPipeline();
};

/* ---------- console ---------- */
function classify(line){
  const t = line.trim();
  if(/failed|error|FAILED|exited/i.test(t) && !/0 failed/.test(t)) return 'error';
  if(t.startsWith('[personas]')) return 'persona';
  if(t.startsWith('[improve]')) return 'improve';
  if(t.startsWith('[codefix]')) return 'code';
  if(/^\[\d{3} .*judg/i.test(t) || /judge\[/.test(t)) return 'judge';
  if(/^\[\d{3} /.test(t)) return 'interview';
  return 'system';
}
const TAG_LABEL = {persona:'persona', interview:'interview', judge:'judge',
                   improve:'improver', code:'code', system:'system', error:'error'};
function renderLog(text){
  if(state.paused) return;
  const lines = text.split('\n').slice(state.cleared);
  const q = state.search.toLowerCase();
  const body = $('#con-body');
  const stick = state.autoscroll;
  body.innerHTML = lines.filter(l => l.trim()).map(l => {
    const c = classify(l);
    if(state.filter !== 'all' && c !== state.filter &&
       !(state.filter === 'error' && c === 'error')) return '';
    if(q && !l.toLowerCase().includes(q)) return '';
    return `<div class="ll ${c}"><span class="tag">${TAG_LABEL[c]}</span><span class="body">${
      l.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</span></div>`;
  }).join('');
  if(stick) body.scrollTop = body.scrollHeight;
}
document.querySelectorAll('.chip').forEach(ch => ch.onclick = () => {
  document.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
  ch.classList.add('on'); state.filter = ch.dataset.f; renderLog(lastLog);
});
$('#con-search').oninput = e => { state.search = e.target.value; renderLog(lastLog); };
$('#con-scroll').onclick = e => {
  state.autoscroll = !state.autoscroll;
  e.target.classList.toggle('on', state.autoscroll);
};
$('#con-pause').onclick = e => {
  state.paused = !state.paused;
  e.target.classList.toggle('on', state.paused);
  if(!state.paused) renderLog(lastLog);
};
$('#con-copy').onclick = () => navigator.clipboard.writeText(lastLog).catch(()=>{});
$('#con-clear').onclick = () => { state.cleared = lastLog.split('\n').length; renderLog(lastLog); };
$('#con-full').onclick = () => $('#console').classList.toggle('fullscreen');
$('#sb-toggle').onclick = () => $('#sidebar').classList.toggle('collapsed');
document.querySelectorAll('.imp-tile').forEach(tile => tile.onclick = () => {
  const panel = document.getElementById(tile.dataset.panel);
  const opening = panel.classList.contains('closed');
  // one cycle open at a time
  document.querySelectorAll('.imp-panel').forEach(p => p.classList.add('closed'));
  document.querySelectorAll('.imp-tile').forEach(t => t.classList.remove('on'));
  if(opening){ panel.classList.remove('closed'); tile.classList.add('on'); }
});
document.querySelectorAll('.nav-item[data-view]').forEach(item => item.onclick = () => {
  document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
  item.classList.add('active');
  document.body.className = 'view-' + item.dataset.view;
  if(item.dataset.view === 'logs') renderLog(lastLog);
  if(item.dataset.view === 'chat' && !chat.loaded) loadChat();
});

/* ---------- Requirement Bot chat ---------- */
const chat = {loaded:false, busy:false, complete:false};
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
function renderChat(d){
  chat.complete = d.complete;
  const t = $('#chat-thread');
  t.innerHTML = d.messages.map(m =>
    `<div class="msg ${m.role === 'human' ? 'human' : 'ai'}">
       <div class="who">${m.role === 'human' ? 'You' : 'RequirementsBot'}</div>${esc(m.text)}</div>`
  ).join('')
  + (d.error ? `<div class="msg err">${esc(d.error)}</div>` : '')
  + (d.saved ? `<div class="msg sys">✓ Interview complete — full brief saved to ${d.saved}</div>` : '')
  + (chat.busy ? `<div id="chat-typing"><span class="spin"></span>RequirementsBot is thinking…</div>` : '');
  t.scrollTop = t.scrollHeight;
  $('#chat-send').disabled = chat.busy || chat.complete;
  $('#chat-input').disabled = chat.complete;
}
async function loadChat(){
  chat.loaded = true;
  try{ renderChat(await (await fetch('/chat/history')).json()); }
  catch(e){ chat.loaded = false; }
}
async function sendChat(){
  const inp = $('#chat-input'), text = inp.value.trim();
  if(!text || chat.busy || chat.complete) return;
  chat.busy = true; inp.value = '';
  // optimistic echo while the bot works
  const t = $('#chat-thread');
  t.insertAdjacentHTML('beforeend',
    `<div class="msg human"><div class="who">You</div>${esc(text)}</div>
     <div id="chat-typing"><span class="spin"></span>RequirementsBot is thinking…</div>`);
  t.scrollTop = t.scrollHeight;
  $('#chat-send').disabled = true;
  try{
    const d = await (await fetch('/chat/send', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message: text})})).json();
    chat.busy = false; renderChat(d);
  }catch(e){
    chat.busy = false;
    document.getElementById('chat-typing')?.remove();
    t.insertAdjacentHTML('beforeend',
      `<div class="msg err">Request failed — is the server still running?</div>`);
    $('#chat-send').disabled = false;
  }
  inp.focus();
}
async function uploadChatFiles(fileList){
  const files = await Promise.all([...fileList].map(f => new Promise(res => {
    const r = new FileReader();
    r.onload = () => res({name: f.name, data_b64: r.result.split(',')[1]});
    r.readAsDataURL(f);
  })));
  if(!files.length) return;
  const t = $('#chat-thread');
  try{
    const d = await (await fetch('/chat/upload', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({files})})).json();
    if(d.saved && d.saved.length)
      t.insertAdjacentHTML('beforeend',
        `<div class="msg sys">📎 Uploaded to uploads/: ${esc(d.saved.join(', '))} — now tell the bot the files are ready.</div>`);
    (d.rejected||[]).forEach(msg =>
      t.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(msg)}</div>`));
  }catch(e){
    t.insertAdjacentHTML('beforeend', `<div class="msg err">Upload failed.</div>`);
  }
  t.scrollTop = t.scrollHeight;
  $('#chat-file').value = '';
}
$('#chat-attach').onclick = () => $('#chat-file').click();
$('#chat-file').onchange = e => uploadChatFiles(e.target.files);
$('#chat-thread').addEventListener('dragover', e => {
  e.preventDefault(); e.currentTarget.classList.add('drop');
});
$('#chat-thread').addEventListener('dragleave', e =>
  e.currentTarget.classList.remove('drop'));
$('#chat-thread').addEventListener('drop', e => {
  e.preventDefault(); e.currentTarget.classList.remove('drop');
  uploadChatFiles(e.dataTransfer.files);
});
$('#chat-send').onclick = sendChat;
$('#chat-input').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); }
});
$('#chat-reset').onclick = async () => {
  if(chat.busy) return;
  const d = await (await fetch('/chat/reset', {method:'POST'})).json();
  chat.complete = false; renderChat(d); $('#chat-input').disabled = false;
};

/* ---------- polling ---------- */
let lastLog = '';
async function tick(){
  let d;
  try{ d = await (await fetch('/data')).json(); }catch(e){ return; }
  state.stages = d.stages || [];
  state.lanes = d.lanes || [];
  $('#run-id').textContent = d.run_id ? 'Run #' + d.run_id : 'no runs yet';
  $('#run-meta').textContent = d.started_at
    ? '· started ' + fmtElapsed(Math.floor(Date.now()/1000 - d.started_at)) + ' ago' : '';
  state.elapsed = d.elapsed;
  state.lastActivityAgo = d.last_activity_ago;

  // cycle counters, derived from each lane's stage states
  const laneSt = state.lanes.map(l => {
    const seq = [l.persona, l.interview, l.judge, l.improve, l.codefix];
    if(seq.some(s => s.status === 'running')) return 'running';
    if(seq.some(s => s.status === 'failed')) return 'failed';
    if(l.codefix.status === 'completed') return 'completed';
    return 'queued';
  });
  const nRun = laneSt.filter(s => s === 'running').length;
  const nFail = laneSt.filter(s => s === 'failed').length;
  const nDone = laneSt.filter(s => s === 'completed').length;

  let overall;
  if(!state.lanes.length) overall = d.overall;
  else if(nRun > 0 || d.overall === 'running') overall = 'running';
  else if(nFail > 0) overall = nDone > 0 ? 'completed_failures' : 'failed';
  else overall = d.overall === 'idle' ? 'idle' : 'completed';
  const overallTxt = STATUS_TXT[overall] || overall;
  $('#run-badge').className = 'badge ' + overall;
  $('#run-badge-txt').textContent = overallTxt;
  $('#s-state').className = 'badge ' + overall;
  $('#s-state-txt').textContent = overallTxt;

  $('#s-cost').textContent = '$' + (d.total_cost||0).toFixed(2);
  $('#s-in').textContent = fmtTok(d.total_in||0);
  $('#s-out').textContent = fmtTok(d.total_out||0);
  $('#s-elapsed').textContent = fmtElapsed(d.elapsed);
  $('#s-running').textContent = nRun;
  $('#s-running').classList.toggle('hot', nRun > 0);
  $('#s-failed').textContent = nFail;
  $('#s-failed').classList.toggle('hot', nFail > 0);
  $('#s-completed-c').textContent = nDone;
  renderPipeline();
  if(state.selected) {
    const wasOpen = $('#inspector').classList.contains('open');
    if(wasOpen) openInspector(state.selected);
  }
  lastLog = d.log || '';
  renderLog(lastLog);
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


# ---------- live chat with RequirementsBot (the same bot main.py runs) ----------
CHAT = {"bot": None, "turn": None, "busy": False, "shown": []}
CHAT_LOCK = threading.Lock()


def _chat_bot():
    """Lazy: importing requirements_bot pulls langchain — only pay for it
    when the chat page is actually used."""
    if CHAT["bot"] is None:
        from requirements_bot import RequirementsBot
        bot = RequirementsBot()
        bot.uploads_dir.mkdir(exist_ok=True)
        CHAT["bot"] = bot
    return CHAT["bot"]


def _save_brief(partial: bool) -> None:
    bot, turn = CHAT["bot"], CHAT["turn"]
    if bot is None or turn is None:
        return
    brief = bot.brief(turn)
    if partial:
        brief["partial"] = True
    (ROOT / "requirements_brief.json").write_text(
        json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")


def chat_history() -> dict:
    # bot.chat_history holds the model's raw JSON turns; CHAT["shown"] keeps
    # the clean conversational texts that send() returns (what main.py prints)
    if not CHAT["shown"]:
        from requirements_bot import RequirementsBot
        CHAT["shown"] = [{"role": "ai", "text": RequirementsBot.GREETING}]
    complete = CHAT["bot"].complete if CHAT["bot"] else False
    return {"messages": CHAT["shown"], "complete": complete,
            "busy": CHAT["busy"]}


def chat_send(message: str) -> dict:
    with CHAT_LOCK:
        if CHAT["busy"]:
            return {"error": "The bot is still answering — wait a moment."}
        CHAT["busy"] = True
    try:
        bot = _chat_bot()
        chat_history()  # ensure the greeting is seeded before appending
        CHAT["shown"].append({"role": "human", "text": message})
        msgs, turn = bot.send(message)
        CHAT["shown"] += [{"role": "ai", "text": m} for m in msgs]
        CHAT["turn"] = turn
        if bot.complete:
            _save_brief(partial=False)
        return chat_history() | {
            "saved": "requirements_brief.json" if bot.complete else None}
    except RuntimeError as e:
        # materials gate: the bot refuses to interview blind over unreadable
        # files — surface it in the thread instead of a 500
        return chat_history() | {"error": f"Stopped — {e}. "
                                 "Fix or remove the files in uploads/."}
    except Exception as e:
        return chat_history() | {"error": f"{type(e).__name__}: {e}"}
    finally:
        CHAT["busy"] = False


ALLOWED_EXTS = {".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def chat_upload(files: list) -> dict:
    """Save attached files into uploads/ so the bot's material scan sees them."""
    import base64
    updir = ROOT / "uploads"
    updir.mkdir(exist_ok=True)
    saved, rejected = [], []
    for f in files[:20]:
        name = Path(str(f.get("name", ""))).name  # strip any path components
        ext = Path(name).suffix.lower()
        if not name or ext not in ALLOWED_EXTS:
            rejected.append(f"{name or '(unnamed)'} — only "
                            + " ".join(sorted(ALLOWED_EXTS)) + " are readable")
            continue
        try:
            data = base64.b64decode(str(f.get("data_b64", "")), validate=True)
        except Exception:
            rejected.append(f"{name} — could not decode")
            continue
        if len(data) > 15 * 1024 * 1024:
            rejected.append(f"{name} — larger than 15MB")
            continue
        (updir / name).write_bytes(data)
        saved.append(name)
    return {"saved": saved, "rejected": rejected}


def chat_reset() -> dict:
    with CHAT_LOCK:
        if CHAT["busy"]:
            return {"error": "The bot is still answering — wait a moment."}
        if CHAT["bot"] is not None and not CHAT["bot"].complete:
            _save_brief(partial=True)  # don't lose a half-finished interview
        CHAT.update(bot=None, turn=None, shown=[])
    return chat_history()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data":
            body = json.dumps(payload()).encode("utf-8")
            ctype = "application/json"
        elif self.path == "/chat/history":
            body = json.dumps(chat_history()).encode("utf-8")
            ctype = "application/json"
        else:
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}
        if self.path == "/chat/send":
            out = chat_send(str(req.get("message", "")).strip())
        elif self.path == "/chat/upload":
            out = chat_upload(req.get("files") or [])
        elif self.path == "/chat/reset":
            out = chat_reset()
        else:
            out = {"error": "unknown endpoint"}
        body = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the console quiet
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
```

---

### `requirements.txt`

```text
langchain-anthropic==1.7.1
langchain-core==1.6.2
anthropic==1.4.0
python-dotenv
pydantic==2.13.5
```

