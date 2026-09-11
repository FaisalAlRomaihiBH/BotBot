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
