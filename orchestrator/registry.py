# registry.py — the single authoritative capability registry.
#
# What the radial Home renders is exactly this list: real capabilities with
# observed availability, and roadmap entries unmistakably marked planned and
# non-executable. Registry presence is never authorization — the controller
# checks caller, mode, scope and budget on every use.
import os
from pathlib import Path

from orchestrator import store, supervisor

ROOT = Path(__file__).parent.parent


def capabilities() -> list[dict]:
    api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
    last = store.last_provider_event()
    caps = [
        {
            "id": "requirements_bot",
            "name": "RequirementsBot",
            "kind": "production_specialist",
            "purpose": "Interviews a business owner and produces the requirements brief",
            "contract": "reqbot-io@1",
            "installed": True,
            "enabled": True,
            "planned": False,
            "configured": api_key_present,
            "last_observed": last and {"purpose": last["purpose"],
                                       "error": last["error"], "ts": last["ts"]},
        },
        {
            "id": "materials_analyzer",
            "name": "Materials Analyzer",
            "kind": "internal_helper",
            "purpose": "Distills uploaded chat exports/screenshots for the interviewer",
            "installed": True, "enabled": True, "planned": False,
            "configured": api_key_present,
        },
        {
            "id": "ai_supervisor",
            "name": "AI Supervisor",
            "kind": "management_agent",
            "purpose": "Advisory project manager: explains state, reviews drafts, "
                       "recommends next steps",
            "installed": True, "planned": False,
            "enabled": supervisor.mode() == "advisory",
            "configured": api_key_present,
            "mode": supervisor.mode(),
        },
        # Roadmap: planned, not implemented, non-executable. Testing and
        # improvement will be their own bots connected to the Orchestrator.
        {"id": "evaluation_agent", "name": "Evaluation Bot",
         "kind": "production_specialist", "purpose": "Tests and improves the "
         "other bots through the Orchestrator", "installed": False,
         "enabled": False, "planned": True},
        {"id": "architecture_agent", "name": "Architecture Agent",
         "kind": "production_specialist", "purpose": "Designs the chatbot from "
         "an approved brief", "installed": False, "enabled": False,
         "planned": True},
        {"id": "builder_agent", "name": "Builder Bot",
         "kind": "production_specialist", "purpose": "Builds the chatbot from "
         "the architecture", "installed": False, "enabled": False,
         "planned": True},
    ]
    return caps
