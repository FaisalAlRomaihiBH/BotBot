# orchestrator — the deterministic execution layer around RequirementsBot.
#
# Modules:
#   store       durable SQLite repository (projects, sessions, revisions, events…)
#   controller  the ONLY authority over operational state: runs interview turns,
#               commits revisions, computes readiness, records approvals/exports
#   supervisor  the AI Orchestrator Bot in advisory mode (optional at runtime)
#   registry    the capability registry (what exists, what is merely planned)
