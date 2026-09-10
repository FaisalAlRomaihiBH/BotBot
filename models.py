# models.py — the data schemas of RequirementsBot.
from typing import Optional, Union

from pydantic import BaseModel

# The explicit "we asked, the answer is genuinely indeterminate" value. Without
# it, "the owner never told us" and "the owner told us it varies every year"
# both serialize as null, and a builder cannot tell them apart. Use it as the
# first token of an entry: "unknown-varies — holidays move every year".
UNKNOWN_VARIES = "unknown-varies"


class ServiceOffer(BaseModel):
    """One service/product line with the numbers a bot needs before it may quote.
    services_and_pricing accepts these OR plain strings, so older briefs (and a
    quick free-text capture) stay valid."""
    name: str                                   # "themed cupcakes", "rush order surcharge"
    price: Optional[str] = None                 # exactly as the owner stated it
    price_basis: Optional[str] = None           # "fixed" | "from" (starting price) | "quote only"
    minimum_order_quantity: Optional[str] = None    # "6 units", "none"
    fee_trigger_condition: Optional[str] = None     # what makes this fee apply
                                                    # ("orders under 48h notice")
    lead_time: Optional[str] = None             # the turnaround THIS entry assumes. A
                                                # surcharge without it is meaningless —
                                                # rush pricing needs its rush definition
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
    notes: Optional[list[str]] = None          # anything else notification-specific


class BusinessRequirements(BaseModel):
    """The intake form. Every field Optional because it starts empty and fills up."""
    contact_name: Optional[str] = None           # the person we're interviewing
    business_name: Optional[str] = None
    industry: Optional[str] = None
    business_description: Optional[str] = None
    problem_to_solve: Optional[str] = None       # WHY they want a chatbot
    target_audience: Optional[str] = None        # who will talk to it
    channels: Optional[list[str]] = None         # where the BOT should live (WhatsApp, Instagram...);
                                                 # other ways customers reach the business today
                                                 # belong in business_description
    integrations: Optional[list[str]] = None     # booking system, CRM, order DB...
    conversation_volume: Optional[str] = None    # rough conversations/day, hours coverage
    languages: Optional[list[str]] = None
    success_criteria: Optional[str] = None       # what "working" means to them
    solution_scope: Optional[str] = None         # simple Q&A / lead capture vs. full booking+payment app
    constraints: Optional[list[str]] = None      # compliance, privacy (GDPR), approvals, tech limits
    budget: Optional[str] = None
    timeline: Optional[str] = None
    # --- the chatbot's actual knowledge, gathered during the interview ---
    services_and_pricing: Optional[list[Union[ServiceOffer, str]]] = None
                                                      # every service, price, discount, surcharge,
                                                      # subscription. Prefer a ServiceOffer object per line
                                                      # so a minimum order, a fee's trigger, "from" vs. fixed
                                                      # pricing and the lead time it assumes are separable;
                                                      # plain strings still validate for back-compat
    service_durations: Optional[list[str]] = None     # how long each service takes ("alignment: 45 min"),
                                                      # needed to place bookings on a calendar. Use
                                                      # "unknown-varies — <reason>" when the owner answered
                                                      # but the duration genuinely varies; null means UNASKED
    holiday_closures: Optional[list[str]] = None      # dates/rules the business is shut outside its normal
                                                      # weekly hours ("closed on holiday Mondays", "Aug 1-15").
                                                      # "unknown-varies — <reason>" when the owner answered
                                                      # "it changes every year"; null means UNASKED
    notification_settings: Optional[NotificationSettings] = None  # reminder/notification behaviour
    partners_and_referrals: Optional[list[str]] = None  # work sent OUT to others and partners relied upon
                                                      # ("transmissions go to the Alameda shop", "towing
                                                      # via Joe's") — the bot must route these, not quote them
    faq_answers: Optional[list[str]] = None           # confirmed question->answer pairs the bot can use
    business_policies: Optional[list[str]] = None     # cancellations, rush requests, coverage area, VAT...
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
    certifications_and_standards: Optional[list[str]] = None  # quality/regulatory standards, one per entry:
                                                      # "<standard> — held since <when> / under consideration
                                                      # — <cost, timeline, who audits, why>". Held and
                                                      # aspirational both belong here, never in faq_answers
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
    owner_sentiment_or_concerns: Optional[list[str]] = None  # LEGACY MIRROR of adoption_risks +
                                                      # background_color, auto-filled by RequirementsBot so
                                                      # older consumers keep working. Write the two fields
                                                      # above instead of this one
    maintenance_and_ownership: Optional[str] = None   # who updates the bot's content after launch, how
                                                      # often, appetite for paid managed updates, and what
                                                      # training the owner needs
    # --- provenance: which side of the interview a fact came from ---
    facts_from_uploads: Optional[list[str]] = None    # facts taken from the UPLOADED MATERIALS (quoted
                                                      # turnaround times, informal discounts, unanswered
                                                      # threads). Auto-seeded from the materials analysis so
                                                      # they can be cross-checked against the transcript
    fact_conflicts: Optional[list[str]] = None        # contradictions between sources, never silently
                                                      # merged: "hours — materials: 'most weekdays' vs.
                                                      # owner: 'evenings only' — unresolved"
    # --- follow-ups, split by WHO must act (open_items is an auto-built index) ---
    unresolved_business_facts: Optional[list[str]] = None   # facts the OWNER must still supply (IBAN,
                                                            # kids-menu prices, exact opening hours)
    pending_design_decisions: Optional[list[str]] = None    # bot/product decisions still open (how to
                                                            # handle a thumbs-up reply, tone, fallback)
    customer_replies_owed: Optional[list[str]] = None       # replies the owner owes real customers,
                                                            # surfaced from the materials
    open_items: Optional[list[str]] = None            # NOT a bucket to choose: RequirementsBot rebuilds this
                                                      # every turn as a labelled index of all three lists
                                                      # above. Strictly things the DELIVERY TEAM must action
                                                      # or chase — never an internal debate the business has
                                                      # with itself, and never an uncertainty nobody voiced
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
