# models.py — the data schemas of RequirementsBot.
from typing import Optional

from pydantic import BaseModel


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
    faq_answers: Optional[list[str]] = None           # confirmed question->answer pairs the bot can use
    business_policies: Optional[list[str]] = None     # cancellations, rush requests, coverage area, VAT...
    escalation_rules: Optional[list[str]] = None      # what goes to a human, to whom, via what channel
    open_items: Optional[list[str]] = None            # things the owner was unsure about / left unresolved
    additional_notes: Optional[list[str]] = None      # important facts that fit no other field — never lose a fact


class ConversationAnalysis(BaseModel):
    """What the analysis of uploaded materials produces."""
    inquiry_categories: list[str]    # e.g. "Appointment booking (~40%): customers ask for slots"
    resolved_patterns: list[str]     # inquiries where the materials SHOW how staff answers
    knowledge_gaps: list[str]        # inquiries whose resolution is NOT visible -> ask the owner
    facts_learned: list[str]         # hard facts about the business found in the materials


class InterviewTurn(BaseModel):
    """What the interviewer model must output EVERY turn."""
    requirements: BusinessRequirements   # the form, updated with everything learned so far
    next_message: str                    # the question (or final summary) to show the user
    interview_complete: bool             # True only when enough is gathered and confirmed
    run_file_analysis: bool = False      # True when the owner just said their files are ready
