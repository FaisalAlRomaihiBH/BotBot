# Orchestrator V1 — Authoritative Design

Status: implementation-ready design; **no application code has been changed**. Companion documents: [current-state-and-preservation.md](current-state-and-preservation.md) (baseline evidence), [ai-supervisor.md](ai-supervisor.md), [contracts-and-schema.md](contracts-and-schema.md), [dashboard.md](dashboard.md), [migration-and-tests.md](migration-and-tests.md), [operations-and-evaluation.md](operations-and-evaluation.md), [decisions.md](decisions.md). Where documents could disagree, this file and contracts-and-schema.md are authoritative; the others cross-reference rather than redefine.

## 1. Source hierarchy and traceability

1. **The design prompt (BotBot_Final_Design_Prompt_v3)** defines approved target requirements.
2. **The live repository** (commit `6034853`) establishes what is implemented — evidence in current-state-and-preservation.md.
3. **HANDOFF_FOR_AI.md** is a snapshot; it matches the live repo today, and any future divergence is resolved in favor of the live code.
4. The optional CX Foundation transcripts were not available and were skipped per the operator's instruction (2026-09-13); their ideas are represented only as the design prompt already restates them (three layers, small pilots, governance, monitoring). No claim in this design attributes an implementation decision to those talks.

Recorded contradiction: none found between the prompt's baseline description and the live repository. One nuance: the prompt says the snapshot "can rewrite interviewer_prompt.txt and push changes to main" — confirmed live and *stronger* than stated: the rewritten prompt is committed carrying the previous prompt's score and is never itself evaluated (risk R1, preservation doc §2). Treated as a defect to correct, with the correction listed as an intentional change.

## 2. Mission and V1 scope

Build a hybrid **Orchestrator** around the existing RequirementsBot:

- **AI Orchestrator Bot (the supervisor)** — management intelligence: explains recorded progress, reviews suspected gaps/conflicts, proposes next steps. Optional at runtime; V1 mode is **advisory/read-only**. Fully specified in ai-supervisor.md.
- **Deterministic Python execution controller** — the *only* authority over operational state: validates, sequences, persists, enforces contracts/limits, records approvals.

Two AI roles total. RequirementsBot remains the single production specialist; MaterialsAnalyzer remains its internal helper; the persona/judge/improver tools remain isolated testing utilities. No Architecture/Builder/Frontend/Repair/Deployment bots are built — only registration interfaces for later.

Target workflow:

```
User selects/creates project
  → controller runs the existing requirements interview
  → validated, versioned requirements draft (SpecRevision)
  → explicit human review (Needs Attention)
  → approved handoff package for a future Architecture Agent
```

Separate operator workflow: operator asks about the project → supervisor reads authorized state/evidence → grounded explanation or structured recommendation → controller validates and records the advisory result. **Operator** (manages the factory) and **business owner** (supplies requirements) are distinct roles even when one local person plays both; role is recorded on every actor-attributed record. Normal owner messages go controller → RequirementsBotAdapter directly, with **no supervisor call**.

### 2.1 Outcomes and non-goals

The factory's V1 outcome is a **faithful, traceable requirements package** with unresolved decisions visible and an explicit approval boundary. Customer success criteria for the *future* chatbot stay inside `BusinessRequirements.success_criteria`; factory metrics never enter `BusinessRequirements` (scorecard: operations-and-evaluation.md §2).

A customer requirement like "my bot should take deposits" is *recorded* (policy, authorization, success conditions, exceptions — the schema already carries `payment_collection_requirements`); the factory never moves money, places bookings, or messages the customer's customers. A requested integration is a requirement in the brief, never an installed factory capability (registry rules, contracts-and-schema.md §4).

Non-goals for V1: production deployment, generated customer apps, autonomous scheduled training, automatic prompt/code promotion, new live connectors (CRM/calendar/payments), multi-user shared access (flagged as an open product decision — decisions.md §4).

## 3. Three logical layers (responsibilities, not services)

| Layer | Components | Responsibility |
|---|---|---|
| **Intelligence** | RequirementsBot (existing); AI Orchestrator Bot (new, advisory) | Interpret authorized input, extract or review meaning, explain evidence, propose next steps. |
| **Integration** | RequirementsBotAdapter, SupervisorAdapter, scoped read tools, materials manifest service, persistence interfaces | Move permitted inputs/outputs through typed interfaces and configured dependencies. |
| **Orchestration** | Deterministic execution controller | Authorize and sequence operations, enforce contracts, own durable state, budgets, retries, approval rules. |

The dashboard's central Orchestrator circle represents the supervisor **and** the controller together (dashboard.md §3). The integration layer is supporting code — not a manager, not a separate circle, not a service. Everything runs co-located in one Python application; distinct responsibility, one process.

Invariants:
- AI recommendations never bypass rules; a model statement ("approved", "completed", "booking confirmed") changes no authoritative state.
- Adapters cannot grant themselves permission.
- Agents see specific authorized records, not "everything" — access is scoped per request.
- Memory, measurement, candidate improvement, and release promotion are separate mechanisms (operations-and-evaluation.md).

## 4. Module layout (proposed; all new code under `orchestrator/`)

```
orchestrator/
  controller.py        # deterministic execution controller: run lifecycle, gates, approvals
  contracts.py         # typed request/result/proposal/decision models (contracts-and-schema.md)
  registry.py          # CapabilityDefinition + CapabilityAvailability (contracts-and-schema.md §4)
  store.py             # durable repository over SQLite (see §6); revisions, runs, events
  events.py            # event journal write/read, cursors
  revisions.py         # SpecRevision service: snapshot, diff, immutability
  readiness.py         # versioned readiness rubric (§10 rules)
  review.py            # ReviewRequest lifecycle, approvals, export service
  alerts.py            # deterministic alert policy + records (operations doc §4)
  adapters/
    requirements_bot.py  # RequirementsBotAdapter (§7)
    supervisor.py        # SupervisorAdapter + scoped read tools (ai-supervisor.md)
  materials.py         # per-project manifest, hashing, storage (§ contracts doc §6)
  api.py               # namespaced HTTP API + legacy-route adapters (contracts doc §8)
config/
  supervisor_prompt_v1.md   # versioned supervisor instruction (draft in ai-supervisor.md §9)
  pricing.yaml              # versioned pricing table (replaces silent constants)
  policies.yaml             # thresholds, budgets, alert rules — versioned
```

Modules, not microservices. The existing seven top-level files are not moved in V1 (relocation is cosmetic and deferred; migration-and-tests.md phases). For each component, responsibility/inputs/outputs/authority/failure behavior are specified in contracts-and-schema.md §2.

### 4.1 Frameworks considered

- **LangGraph**: rejected for V1. Its benefit (graph-structured multi-agent state) duplicates what the controller must own anyway; two state machines (application + graph) would compete over completion. Revisit only if bounded delegation later produces genuinely branching agent graphs. (decisions.md D3.)
- **FastAPI**: optional. The existing `BaseHTTPRequestHandler` works; the new API's needs (JSON bodies, routing, request size limits) are modest. Default: keep stdlib `ThreadingHTTPServer` with a small router to avoid a dependency migration in the same phase as behavior-critical changes; FastAPI is an approved-if-desired swap in the API phase. (decisions.md D4.)

## 5. Logical flows

**A. Business interview** (no supervisor involvement):
```
Dashboard chat / CLI → api.py → controller.run_interview_turn()
  → RequirementsBotAdapter → RequirementsBot.send() → MaterialsAnalyzer / Claude
  → adapter returns typed result → controller validates, diffs, commits SpecRevision
  → durable events → UI polling picks up state
```

**B. Operator management** (explicit trigger only):
```
Orchestrator panel chat → api.py → controller.supervisor_request()
  → SupervisorAdapter (bounded context + scoped read tools) → Claude
  → typed SupervisorResult → controller validates evidence refs, persists advisory records
  → operator sees grounded answer / findings / proposals
```

**C. Future permitted delegation** (extension contract, not V1 behavior):
```
Supervisor ActionProposal → controller validation + policy → explicit approval (when required)
  → revalidation at execution time → registered specialist adapter → validated result → durable event
```

## 6. Persistence

### 6.1 Store selection

**V1 default: SQLite** (stdlib `sqlite3`, WAL mode, one file per installation under `orchestrator_data/botbot.db`).

| Criterion | SQLite | PostgreSQL |
|---|---|---|
| Matches current deployment (single local operator, localhost server) | ✅ zero-install | ❌ service to run |
| Transactions, foreign keys, durability | ✅ sufficient | ✅ |
| Concurrent writers | Adequate (WAL; writes serialized per project anyway, §6.3) | Better |
| Later multi-user server deployment | Migration needed | Native |

Decision: SQLite now; the repository layer (`store.py`) exposes no SQLite-specific types so a later PostgreSQL migration is a driver swap plus a data migration, listed as a future phase precondition for any shared deployment. One authoritative store — no second database inside the supervisor, no queue.

### 6.2 What is stored (tables may be consolidated where justified; canonical shapes in contracts-and-schema.md §7)

- `projects`, `interview_sessions` (bot state snapshots via existing `to_dict()`), `management_conversations` (explicit `conversation_type`, access scope — supervisor speech is **never** appended to `RequirementsBot.chat_history`).
- `messages` (interview + management, typed), `runs`, `invocations` (every provider call with usage), `events` (journal, §8).
- `spec_revisions` (immutable), `materials` manifest, `review_requests`, `approvals`, `supervisor_requests/results`, `findings` (+dispositions), `action_proposals`, `action_decisions/receipts`, `alerts`.
- Evaluation/candidate/promotion records (operations doc §5) — same store, separate tables, testing-scoped.

### 6.3 Concurrency

Writes are serialized **per project/session** (a per-project lock or single-writer queue), with an optimistic revision check at commit: every accepted turn records `parent_revision_id`; a commit whose parent is no longer head is rejected as a conflict, never merged silently. No global lock across projects. Background work (an in-flight interview turn) is a *persisted* run row with a worker lease + heartbeat, so a restart can detect an orphaned run and mark it `interrupted` rather than losing it; the design does not rely on a bare in-process thread as the only record that work exists.

### 6.4 Imports (nondestructive, idempotent)

- `persona_sessions/*.json` → interview sessions (bot state via existing `from_dict`, which already tolerates missing keys — `requirements_bot.py:310-323`).
- `requirements_brief.json` → a SpecRevision for the default project, provenance `imported-legacy`; missing evidence recorded as `provenance unavailable`, never fabricated.
- `parallel_runs/*` → historical evaluation observations, version metadata `unknown` where absent; no retroactive scores attached to prompts.
- Originals untouched; import keyed by content hash so re-runs are no-ops. The legacy `requirements_brief.json` filename keeps being written for the default project (P9) but is no longer the authoritative store for multiple projects.

## 7. Safe state updates around a mutating bot

`RequirementsBot.send()` mutates instance state in place. The adapter boundary makes that safe:

1. Persist incoming message + idempotency record (key: project, session, client message ID).
2. Claim the run (lease), load the session snapshot at its recorded SpecRevision.
3. **Rehydrate a fresh bot from the snapshot** (`from_dict`) and execute on that isolated working copy, recording each invocation (attempt, usage, error).
4. Validate candidate output (schema, supported version) and compute a **semantic field diff** between the before/after full-form snapshots — the bot keeps emitting full forms (P2); orchestration bookkeeping is derived outside the model contract, never forced into it.
5. Apply the destructive-change policy (§7.1).
6. Atomically commit: new SpecRevision, new bot snapshot, clean display messages, run outcome, events — one transaction; **no transaction is held open across model calls**.
7. Publish UI events only after the durable commit.

Stale-worker protection: the commit re-checks the lease and the parent revision; a cancelled or superseded run's candidate is stored as a `rejected_candidate` artifact (auditable) and does **not** become session state. The controller never rewrites the interview transcript and never pretends a withheld message was shown — messages the completion gates suppress are recorded exactly as the bot already records them (`record_as` markers, `requirements_bot.py:250,267,283`).

### 7.1 Destructive-change policy

A re-emitted full form with a now-null field must not silently erase a previously confirmed fact. Rules, in order:

1. Additions and refinements (null → value; value → longer/more-structured value) commit freely.
2. An explicit owner correction (the turn's message or `fact_conflicts` shows supersession) commits, preserving the prior value in revision history.
3. A disappearance code cannot attribute to (1)/(2) is **conservative**: the prior value is retained in the committed revision, the loss is recorded as a `validation_finding` on the run, and the item surfaces in Needs Attention. No supervisor call is inserted; no per-turn approval is demanded for harmless additions.
4. List identity: entries in object-lists (services, team members, channels…) get **stable entity IDs assigned at first commit**, matched on normalized name keys the codebase already uses (`_norm`, `requirements_bot.py:700-705`); array position is never identity.

A structural diff is evidence of change, not proof a natural-language edit was a supported correction — hence the conservative branch (3) and human review, not silent trust.

### 7.2 Honest failure boundary

Local idempotency prevents duplicate *committed* effects; it cannot guarantee the remote LLM call wasn't executed and billed twice around a crash. Recovery for an unknown provider outcome: the run is marked `outcome_unknown`, usage `unknown` (alert class A3, operations doc §4), and the turn is re-runnable only by explicit operator action — never auto-retried into a possible double conversation turn.

## 8. Events and honest status

All new orchestration state flows from **structured durable events**, not `run.log` regex (fixes R4; the legacy parser remains for historical training runs only, P14). Event envelope and full type list: contracts-and-schema.md §7.2. Core families: message/run/invocation lifecycle, materials, validation, revision, conflict, waiting, review/approval, export, cancellation, failure, supervisor request lifecycle, finding disposition, proposal/decision/receipt, recommendation superseded, alert lifecycle.

Events carry: event ID, project/session/run IDs (as applicable), conversation type, actor role, monotonic sequence/cursor, timestamp, type, sanitized payload. Events distinguish **advice vs authorization vs execution**. Replaying events is a pure read: replay can never trigger a model call.

UI transport: **polling retained** (the page already polls every 2s) with incremental cursors (`?after=<seq>`) and snapshot recovery; SSE is not justified at this scale (decisions.md D5). The UI shows reconnection/staleness explicitly (dashboard.md §7) instead of green-by-default.

Usage accounting: every invocation row records fresh-in / cache-read / cache-write / out (the counters the bot already tracks, `requirements_bot.py:227-229`) plus model and context (interview turn, materials analysis, supervisor request, retry, failed attempt). Totals aggregate from invocation rows exactly once — no double counting of cumulative bot counters. Supervisor usage is a separate visible breakdown, included once in the project total. Pricing from versioned `config/pricing.yaml`; unknown model or missing usage ⇒ displayed **unknown**, never zero, never silently priced as another model (fixes R8). Active-execution time, human-wait time, and evaluation time are separate measures.

## 9. Versioning and pinning

Every orchestrated interview session pins at start: interviewer prompt hash, model ID, schema version, code version (git describe), policy version. `_build_messages` reads the prompt file fresh each turn (R6); under orchestration the adapter supplies the pinned prompt text (mechanism: adapter passes the pinned content, or points `PROMPT_FILE` at an immutable per-version copy — per-instance, never a global monkey-patch of the class attribute, since concurrent bots share the class). The existing live-reload behavior survives, explicitly, only in a marked **development mode** on non-orchestrated sessions (`main.py`, legacy chat), documented rather than silently removed. A promoted candidate never changes a running interview; new sessions adopt it, ongoing sessions stay pinned (operations doc §5).

## 10. Lifecycle state machines

Two separate machines; supervisor request states are a third (ai-supervisor.md §6). Full transition tables with preconditions/writes/events/failure routes: contracts-and-schema.md §7.3.

**Project/interview lifecycle**
```
created → interviewing ⇄ waiting_for_owner
interviewing → interview_closed            (bot sets interview_complete; gates already applied)
interview_closed → review_required         (readiness rubric names blocking gaps — or none)
review_required → approved                 (explicit recorded approval of an exact revision)
approved → revised_after_approval          (any later accepted change; invalidates approval)
any → paused | cancelled                   (explicit operator action)
run-level failures → project stays alive; repeated failure raises an alert, never destroys state
```

**Run execution**: `queued → claimed → executing → validating → committed | rejected | failed | cancelled | interrupted | outcome_unknown`.

Rules the machines enforce:
- **Interview completion ≠ readiness ≠ approval.** `interview_complete=true` (including after refusals or the owner leaving) transitions to `interview_closed`; it never implies build-ready. Partial export is available from every state. Approval is an explicit action against an exact SpecRevision. Neither controller nor supervisor may reopen a closed interview; the only path back is an explicit, recorded operator action creating a *new* session that references the old one.
- **Waiting invokes nothing.** `waiting_for_owner` and unassigned reviews consume zero model calls. Waiting is not "stuck"; the deadline alert (A2) applies only to *actively executing* runs.
- **Readiness** is computed in code from a versioned rubric (`readiness.py`): applicability-aware named gaps (money features ⇒ payment mechanics required; scheduling scope ⇒ scheduling_requirements required — mirroring the checks `_postprocess` already generates), producing applicable/covered/unknown/not-applicable counts. Asked-and-refused budget/timeline keep their existing recorded meaning (`budget_confidence="refused"` passes the interview gate, `requirements_bot.py:500`, and appears as a visible non-blocking note, not a silent pass). No raw non-null percentage, no LLM confidence, no arbitrary 90% threshold.
- **Retry coordination.** Existing inner retries stay: `_ask` 3 attempts (`requirements_bot.py:581`), `MaterialsAnalyzer.analyze` 3 (`:115`), SDK `max_retries` on testing LLMs. The controller adds **no automatic outer retry** of a failed run (bounded total work; avoids multiplication); it records the failure and offers explicit re-run. Budget admission control per run (ai-supervisor.md §7 for supervisor; same mechanism for interviews) reserves a conservative allowance and reconciles measured usage after.
- **Pause/cancel** takes effect at the next safe boundary (before commit); the UI never claims an in-flight provider call was instantly cancelled or unbilled. A late result lands against its original revision as `stale` (example 5, ai-supervisor.md §8).

## 11. Security boundaries (summary; full treatment in contracts doc §9 and ai-supervisor.md)

- Server-side enforcement: project scoping, role permission checks, upload limits (existing checks preserved: extension allowlist, name sanitization, 15MB — `dashboard.py:1297-1311`), secret redaction in events/logs, HTML-escaping of user/model text in the UI (the page already escapes via `esc()`; the new views must too).
- Localhost-only remains the default; shared access requires a separately designed auth phase (open decision).
- LLM outputs are untrusted input everywhere: paths, evidence IDs, states, scores, "readiness" claims. Uploaded materials are evidence, never instructions (the interviewer prompt already treats them as data; the supervisor contract enforces the same — ai-supervisor.md §5). Deterministic checks validate structure and policy; semantic truth still requires human review for high-impact changes.
- Neither AI role may: access other projects, execute shell commands, modify source/prompts, approve its own output, change its own permissions, deploy, or start the legacy improver/codefix pipeline. Enforced in tools and controller policy, not just prompts.

## 12. End-to-end sequences

**S1 — Ordinary interview turn**: owner sends message → controller persists + claims run → adapter rehydrates bot → `send()` (auto-scan sees no material change) → 1 message + full form → diff: 3 additions → commit revision N+1 → events → UI updates. No supervisor call, no review created.

**S2 — Materials turn**: owner: "files are up" → bot sets `run_file_analysis` → analyzer runs (invocation recorded) → two display messages (P3) → upload-tagged facts merge (existing `_merge_upload_facts`) → manifest rows get analysis status + hash → commit.

**S3 — Interview closes with refusals**: owner says goodbye; essentials sweep already spent → `interview_complete=true` with `budget_confidence="refused"` → state `interview_closed` → readiness rubric: 2 blocking gaps (no payment rail for a deposit feature; unresolved fact_conflict) → `review_required`, two ReviewRequests in Needs Attention → partial/full export available throughout.

**S4 — Review and approval**: operator opens Needs Attention → conflict item (booking confirmation, see ai-supervisor.md example 2) → assigned to the business decision-maker role → owner's answer recorded as disposition with evidence → new draft revision if the answer changes facts → operator approves revision N+4 → approval record binds actor + revision hash → export package (contracts doc §8.3).

**S5 — Supervisor advisory review**: ai-supervisor.md §8 examples 1–6.

**S6 — Crash mid-turn**: worker dies after the provider call, before commit → restart detects expired lease → run `interrupted`, provider outcome unknown → alert A3 → operator sees honest state; explicit re-run creates a new run; the owner's message is still persisted, nothing is silently re-sent.

## 13. Requirements-to-design traceability

| Prompt section | Where addressed | Validation |
|---|---|---|
| §1 baseline | current-state-and-preservation.md §1–2 | T-series char. tests (migration doc §2) |
| §2 scope | this doc §2 | scorecard, ops doc §2 |
| §3 bot preservation | preservation matrix P1–P18 | T1–T5 |
| §4 architecture/layers/registry | this doc §3–4; contracts doc §4 | T11, RS1 |
| §4A supervisor | ai-supervisor.md (all) | T12–T18 |
| §5 identifiers | contracts doc §1 | schema review |
| §6 adapter contracts | contracts doc §2, §5 | T6 |
| §7 safe updates | this doc §7 | T6, T7 |
| §8 additive schema | contracts doc §3 | T1, T5 |
| §9 evidence/uncertainty | contracts doc §3.2–3.3 | T6 |
| §10 completion ≠ readiness; review ownership | this doc §10; contracts doc §5.3 | T4, T14, RS3, RS4 |
| §11 state machines | this doc §10; contracts doc §7.3 | T7 |
| §12 persistence/isolation/imports | this doc §6 | T7, T9, RS9 |
| §13 materials/retention | contracts doc §6; ops doc §6 | T3, RS10 |
| §14 events/metrics/alerts | this doc §8; ops doc §3–4 | T8, RS5, RS6 |
| §15–16 dashboard | dashboard.md | T9, T18 |
| §17 training restructure/promotion | ops doc §5; preservation §4.1 | T10, RS7, RS8 |
| §18 APIs/package | contracts doc §8 | T9 |
| §19 security | this doc §11; contracts doc §9 | T15 |
| §20 acceptance/scorecard/pilot | migration doc; ops doc §2 | all |
| §21 deliverables | the eight documents | consistency check (decisions.md §5) |

## 14. Responsibility matrix

| Authority | Operator | AI Supervisor | Execution Controller | RequirementsBot |
|---|---|---|---|---|
| Read project state/evidence | ✅ (own projects) | ✅ scoped, per-request | ✅ | its own session only |
| Propose findings/actions | ✅ | ✅ (advisory records) | — (it decides, it doesn't propose) | proposes form updates via InterviewTurn |
| Approve packages/actions | ✅ (recorded, per revision) | ❌ never | ❌ (validates and records approvals; grants none) | ❌ |
| Execute state transitions | via explicit commands | ❌ | ✅ sole authority | ❌ (its `interview_complete` is a *signal* the controller maps to `interview_closed`) |
| Persist authoritative state | ❌ | ❌ | ✅ sole authority | ❌ (its snapshots are stored *by* the controller) |
| Modify prompts/code/deploy | via separate, explicit promotion workflow | ❌ | ❌ (executes promotion only after recorded human approval) | ❌ |
