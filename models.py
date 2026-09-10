# models.py — the data schemas of RequirementsBot.
from typing import Optional

from pydantic import BaseModel


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
    services_and_pricing: Optional[list[str]] = None  # every service, price, discount, subscription
    service_durations: Optional[list[str]] = None     # how long each service takes ("alignment: 45 min"),
                                                      # needed to place bookings on a calendar
    holiday_closures: Optional[list[str]] = None      # dates/rules the business is shut outside its normal
                                                      # weekly hours ("closed on holiday Mondays", "Aug 1-15")
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
                                                      # above, so nothing hides behind a wrong routing call
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
