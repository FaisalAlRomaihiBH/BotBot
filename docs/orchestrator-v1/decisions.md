# Decisions, Tradeoffs, and Open Questions — Orchestrator V1

## 1. Selected defaults

| ID | Decision | Rationale | Rejected alternative |
|---|---|---|---|
| D1 | SQLite (WAL) as the authoritative store | Matches localhost single-operator reality; transactional; zero-install; repository layer keeps a PostgreSQL path open | PostgreSQL now (service overhead with no current concurrent-deployment need) |
| D2 | One Python application, modules not services | The whole system is one process today; distinct responsibilities don't require distribution | Microservices, queues, Redis |
| D3 | Plain-Python deterministic controller; no LangGraph | The controller must own state anyway; a graph engine would create a second, competing state machine | LangGraph-owned workflow state |
| D4 | Keep stdlib HTTP server + small router; FastAPI optional in the API phase | Avoids a framework migration entangled with behavior-critical phases | Mandatory FastAPI/React rewrite |
| D5 | Polling with cursors; no SSE | Page already polls at 2s; scale is one operator | SSE/WebSockets |
| D6 | Advisory-only supervisor in V1; delegation designed as contracts only | Prompt requirement; risk containment; measurable value first | Bounded delegation at launch |
| D7 | Zero new `BusinessRequirements` fields; governance sidecar carries acceptance criteria, provenance, statuses | Field audit found existing coverage; avoids duplicate homes; back-compat guaranteed by construction | New embedded governance fields |
| D8 | Per-session pinned prompt versions under orchestration; live reload only in explicit dev mode | Fixes mid-interview mutation (risk R6) while preserving the developer convenience visibly | Silent removal of live reload; or keeping global reload |
| D9 | Promotion pipeline with isolated candidates + human release decision; auto improve-and-push retired from the default path | Snapshot finding: candidates were promoted unevaluated with the old prompt's score (ops doc §5.1) | Keeping auto-push with more guardrails |
| D10 | Supervisor reuses the existing `langchain-anthropic` provider integration, model set by config | No provider migration to add a role | Second provider/SDK |
| D11 | Default project absorbs all legacy single-session behavior | Legacy routes stay byte-compatible without cross-project ambiguity | Breaking legacy routes or repurposing `/data` |
| D12 | Alerts are deterministic, in-dashboard only | No external integrations exist; none authorized | Email/Slack notifiers, AI monitoring bot |

## 2. Rejected overengineering (explicit)

Second database inside the supervisor; a "human handoff bot"; a ticketing system separate from ReviewRequests; plugin/discovery infrastructure for future specialists (a registry row + adapter recipe suffices); paid health probes for status dots; per-turn supervisor commentary; automatic outer retries of failed runs; a generic app-factory template replacing the product; duplicating the supervisor chat into a second navigation surface.

## 3. Assumptions (labeled, not decided)

- A1: Single local operator remains the deployment reality through V1 (evidence: `127.0.0.1` binding, no auth anywhere).
- A2: Pricing constants in `parallel_personas.py:38-43` are stale-capable configuration; treated as unverified until refreshed in `pricing.yaml`.
- A3: Model identifiers in code (`claude-sonnet-5`, `claude-opus-5`) are configuration from the snapshot, not verified current provider capabilities.
- A4: Existing `persona_sessions/` and `parallel_runs/` data is worth importing; if the operator prefers a clean start, the importers simply aren't run.

## 4. Unresolved questions — each with an owner and impact (no hidden TBDs)

| Q | Question | Decision owner | Impact if unresolved |
|---|---|---|---|
| Q1 | Will anyone besides the operator ever open the dashboard (shared/customer access)? | Operator (product) | Blocks any auth design; until answered, localhost-only stands and shared deployment is out of scope |
| Q2 | Retention periods per record type (ops doc §6) | Operator, with qualified review for any regulatory concern | Defaults keep everything except explicit withdrawals; storage grows unbounded |
| Q3 | Supervisor model + per-request budget figures | Operator (cost) | Advisory mode ships Disabled until configured; no cost surprises possible |
| Q4 | Threshold values for alerts A1–A6 | Operator | Ship as `provisional` defaults; alerts labeled provisional until baselined |
| Q5 | Is the optional post-closure supervisor review wanted at all? | Operator | Stays off; zero effect |
| Q6 | Budget for the first authorized live evaluation runs (A/B/C comparison) | Operator | Comparison C stays unmeasured; promotion decisions rest on mock+fixture evidence only, labeled as such |

None of these block implementation phases G0–P7; Q1/Q6 gate P8's live parts.

## 5. Documentation consistency check (performed on this document set)

Verified across all eight documents:
- No statement says "the orchestrator has no AI" — the supervisor is a designed V1 role with phase P5.
- No path lets the supervisor control state directly; all writes go through the controller (responsibility matrix, design.md §14).
- Interview closure is never equated with approval (design.md §10; dashboard.md §7; contracts doc §7.3).
- Registry presence grants no permission (contracts doc §4 execution check).
- Review acknowledgment is never resolution (contracts doc §5.1; ops doc §4).
- Customer integration requirements never become live actions (design.md §2.1; RS2).
- Baseline scores are never presented as candidate results (ops doc §5.4; RS7).
- No security/quality policy removes partial exports (available in every lifecycle state), forces endless questioning (one-time gates preserved, T4), or depends on the optional supervisor being available (T11).
