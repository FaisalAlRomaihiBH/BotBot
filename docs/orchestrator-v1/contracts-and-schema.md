# Contracts and Schema — Orchestrator V1

Canonical definitions. Other documents cross-reference these; none redefine them. Nothing here changes `models.py` — all V1 additions are envelope/sidecar records (§3).

## 1. Identifiers and vocabulary

| Term | Meaning | ID form |
|---|---|---|
| Project | The business chatbot being specified | `proj_<ulid>` |
| Interview session | The requirements conversation + serialized bot state | `sess_<ulid>` |
| Management conversation | Operator ↔ supervisor, scoped to one project | `mgmt_<ulid>` |
| Supervisor review/finding | Advisory analysis bound to exact input versions | `srev_/find_<ulid>` |
| Action proposal / decision / receipt | Requested action / controller authorization outcome / actual execution result — never interchangeable | `prop_/adec_/arcp_<ulid>` |
| Workflow | The requirements lifecycle incl. human waiting/review | (state on project) |
| Run | One orchestrated unit (e.g. processing one user message) | `run_<ulid>` |
| Invocation | One provider/model call within a run | `inv_<ulid>` |
| Spec revision | Immutable saved requirements snapshot | `rev_<n>` per project |
| Agent contract | Allowed input/output + permissions for a bot | versioned name, e.g. `reqbot-io@1` |
| Requirements contract | The reviewed product specification for downstream work (not a legal claim) | the approved package, §8.3 |

One user message can produce several invocations (analysis, `_ask` retries, closing gates — see `requirements_bot.py:242-284`): those are one run, many invocations, one user message — and none of them are persona-training cycles.

**Single authority**: only the execution controller transitions operational state and publishes accepted revisions. RequirementsBot's `interview_complete` and every supervisor statement are inputs the controller maps to transitions; neither approves contracts, waives blockers, nor starts agents.

## 2. Component contracts

For each: responsibility / inputs / outputs / authority / failure behavior.

| Component | Responsibility | Inputs | Outputs | Authority | On failure |
|---|---|---|---|---|---|
| Execution controller | Sequence + authorize operations; own state | API commands, adapter results | Transitions, events, decisions | Sole state authority | Fail closed; record; alert |
| RequirementsBotAdapter | Run one interview turn safely (§2.1) | AdapterTurnRequest | AdapterTurnResult | None (proposes candidates) | Typed error result; run `failed` |
| SupervisorAdapter | Run one supervisor request | SupervisorRequest | SupervisorResult / typed error | None | Supervisor-outcome only (ai-supervisor.md §7) |
| Contract validators | Schema + version checks | Candidate payloads | Accept / reject + findings | Reject only | Reject with reasons |
| Durable repository | Transactional persistence | Typed records | Committed rows | Storage only | Transaction rollback |
| Event journal | Append/read ordered events | Events, cursors | Event streams | Append-only | Write failure fails the commit |
| Revision service | Immutable snapshots + diffs | Before/after forms | SpecRevision, field diff | None | Reject commit |
| Readiness policy | Versioned rubric evaluation | A SpecRevision | Named gaps, counts | Blocks approval eligibility only | Conservative: unknown ⇒ not ready |
| Review/approval service | ReviewRequest + approval lifecycle | Dispositions, approvals | Records, events | Records human decisions; grants none itself | Stays open; never auto-approves |
| Materials service | Manifest, hashing, storage | Uploads | Manifest rows, analysis status | None | Fail-loud (preserves existing gate) |
| Alert policy | Deterministic conditions → alerts | Events, records | Alert records | None (visibility only) | Missed evaluation logged |
| API layer | Transport + legacy adapters | HTTP | JSON | None | Typed error responses |

### 2.1 RequirementsBotAdapter I/O (`reqbot-io@1`)

**AdapterTurnRequest** (server-owned; never trusted from any LLM):
```json
{
  "contract_version": "reqbot-io@1",
  "project_id": "proj_…", "session_id": "sess_…", "run_id": "run_…",
  "idempotency_key": "…", "user_message_id": "msg_…",
  "expected_spec_revision": "rev_14",
  "state_snapshot_ref": "snap_…",
  "allowed_material_refs": ["mat_…"],
  "limits": {"max_invocations": 6, "max_seconds": 300}
}
```

**AdapterTurnResult**:
```json
{
  "contract_version": "reqbot-io@1",
  "messages_to_show": ["…"],
  "interview_turn": { /* the bot's full InterviewTurn, verbatim */ },
  "candidate_requirements": { /* post-processed form */ },
  "candidate_bot_snapshot_ref": "snap_…",
  "conversation_analysis_ref": "an_…", "material_manifest_version": 3,
  "observed_changes": [ {"field": "budget", "kind": "added", "entity_id": null} ],
  "validation_findings": [ {"kind": "unsupported_deletion", "field": "faq_answers", "entity_id": "ent_…"} ],
  "evidence_refs": ["msg_…"],
  "invocations": [ {"invocation_id": "inv_…", "purpose": "interview_turn", "usage": {"fresh_in": 0, "cache_read": 0, "cache_write": 0, "out": 0}, "error": null} ],
  "interview_complete_signal": true
}
```
`interview_complete_signal` is explicitly **not** approval or readiness authority. `observed_changes` is derived by the adapter from validated before/after snapshots (design.md §7) — the model contract (`InterviewTurn`) is unchanged and carries no orchestration bookkeeping.

**Validation examples.** Valid: the above. Invalid, rejected with reason:
- payload missing `interview_turn.requirements` → `schema_violation`;
- `contract_version: "reqbot-io@2"` when only `@1` registered → `unsupported_version`;
- result referencing `expected_spec_revision: rev_13` when head is `rev_15` → `revision_conflict` (candidate archived, not applied).

### 2.2 Supervisor contracts

Canonical field lists in ai-supervisor.md §6; the JSON shapes mirror §2.1's conventions (server-owned identity block + model-writable payload). **Server-owned fields are outside the model-writable schema**: the parser for SupervisorResult simply has no fields for project ID, permissions, or revision — values echoed in prose are ignored.

Rejected-example set (each stored as a rejected record with reason code):
- invented evidence ID → `unknown_evidence_ref`;
- foreign project ID in a tool call → `scope_denied`;
- `proposed_actions[].action_type: "run_architecture_agent"` (unregistered) → `capability_unavailable`;
- `requires_approval: false` present in output → field ignored, warning logged (`ignored_model_field`);
- result citing `rev_13` after head moved → stored, displayed `stale`;
- proposal to widen its own `allowed_read_capabilities` → `scope_change_denied`.

## 3. Requirements schema strategy: three separated schemas

- **A. Business requirements** — `models.py` exactly as-is (what the customer wants).
- **B. Requirement-governance metadata** — provenance, identities, gaps, decisions, versions, approvals: **envelope/sidecar** records keyed by `(project, revision, field path, entity_id)`. Never inside `BusinessRequirements`.
- **C. Operational state** — runs, retries, messages, timing, tokens, costs, events: store tables (§7).

Export keeps the legacy shape byte-compatible: `{"requirements": …, "conversation_analysis": …}` (as `brief()` produces, `requirements_bot.py:289-295`), with governance data in a separate envelope (§8.3).

### 3.1 Additive field audit

Candidate gaps audited against the existing ~70 fields. Verdicts:

| Candidate gap | Existing coverage | Verdict |
|---|---|---|
| Info-only vs action capabilities; permissions, confirmation, failure behavior, human handoff | `solution_scope`, `scheduling_requirements.who_confirms`, `payment_collection_requirements`, `escalation_rules` ("none exists — bot should set expectations" is valid per `models.py:382-384`), `notification_settings` | **Covered enough for V1.** Per-feature authorization granularity is real but representable as governance metadata on scope entries; revisit after pilot evidence. No new field now. |
| User roles / authentication requirements | `constraints`, `customer_segments`, `stakeholders` | **Partially covered.** A true gap only for bots with logged-in users — rare in this segment. Defer; capture via `constraints` + governance note. |
| Data collection / prohibited data / retention / consent | `constraints` ("compliance, privacy (GDPR)…", `models.py:304`), `lead_capture_fields` (what to collect + why) | **Mostly covered.** Prohibited-data and retention answers fit `constraints`; a structured sub-model is justified only if pilot briefs show recurring loss. Defer with explicit re-audit trigger. |
| Authoritative knowledge sources, update ownership, freshness, unknown-answer behavior | `current_systems_and_records`, `maintenance_and_ownership`, `faq_answers`, `escalation_rules` | **Covered.** |
| Measurable nonfunctional requirements | `constraints`, `conversation_volume` | **Covered when supplied**; never solicited as invented SLAs. |
| Requirement-level acceptance criteria + success/failure examples | Nothing per-requirement (`success_criteria` is global) | **Genuine gap — resolved in governance layer**: `AcceptanceCriterion` sidecar records referencing requirement paths (§3.2). No `models.py` change. |
| Dependencies / architect-owned deferred decisions | `pending_design_decisions`, `open_strategic_decisions`, `integrations` | **Covered**; handoff package marks architect-owned items (§8.3). |

Result: **zero new `BusinessRequirements` fields in V1.** Owners are never asked to pick databases/frameworks/retrieval tech; not every business answers every field (the prompt's relevance filter stays authoritative, `interviewer_prompt.txt` RELEVANCE FILTER).

### 3.2 Requirement references, evidence, acceptance criteria (governance records)

```
RequirementRef  { ref_id, project_id, field_path, entity_id?, first_revision, label }
Evidence        { evidence_id, kind: message|material|import, source_id, revision_or_hash,
                  location (line/timestamp range), quote?, availability: available|redacted|withdrawn|unavailable }
QuestionRecord  { ref_id?, asked_in_message_id, answered_in_message_id? }
Decision        { decision_id, ref_ids, decided_by (actor+role), decision, reason, revision, ts }
AcceptanceCriterion { ac_id, ref_id, text, examples[], status: proposed|approved|invalidated }
```
No second editable copy of any fact: refs point into the canonical revision. Legacy imports without evidence say `provenance: unavailable` — quotes are never manufactured, and nothing is retro-marked confirmed.

### 3.3 Value status vs asked status

Recorded per `(field path, entity)` in governance metadata, preserving existing null semantics untouched: `unknown_unasked | user_stated | user_confirmed | inferred | assumed | conflicted | deferred | refused | not_applicable`. Literal `unknown-varies` / `unknown - pending confirmation` string values (from `models.py:10-17`) stay in the business schema and map to `deferred`-class statuses in metadata. Existing provenance strings (`[source: uploaded_materials, verified: …]`) are retained for compatibility; approval decisions rely on the trustworthy metadata, not on the tag text — and a model-written `verified: true` is *not* proof of confirmation (the metadata records who confirmed, where). Later correction / revocation / disputed authorization of upload-derived facts flips the linked Evidence rows to `withdrawn`/`disputed` and flags every dependent fact for review (`upload_provenance_status` already carries the owner-facing verdict, `models.py:520-528`).

Prior revisions are immutable; every change records who/what/why/when + affected revision and evidence. A scope change invalidates affected acceptance criteria and approvals (event `approval.invalidated`) — it never silently rewrites an approved package.

## 4. Capability and policy registry

One authoritative source (`registry.py` + seed data); no competing allowlists in prompts, dashboard, or controller code.

**CapabilityDefinition** (static, versioned):
```
{ capability_id, name, purpose,
  kind: production_specialist | management_agent | internal_helper | read_tool | testing_utility,
  implementation_version, config_version,
  input_schema, output_schema, contract_versions: ["reqbot-io@1"], adapter_ref,
  config_refs (SECRET REFERENCES ONLY — never credentials), dependencies, owner,
  permitted_caller_roles, permitted_modes, project_data_scope,
  effect_class: read | side_effect,
  policy_ref { risk_class, approval_required, timeout, retry_policy, idempotency,
               concurrency_limit, call_limits, failure_fallback },
  success_evidence: "what proof of success looks like; how the controller records outcome" }
```

**CapabilityAvailability** (dynamic, separate): `installed, configured, enabled, observed_available (ts) | unknown, unavailable_reason, last_activity`. Installed ≠ enabled ≠ idle ≠ authorized-for-this-operator/project. Observations expire to `unknown`; **no paid health probes to paint a green circle** — availability of LLM-backed capabilities shows last-observed outcome + timestamp.

V1 seed entries: `requirements_bot` (production_specialist, contract `reqbot-io@1`), `materials_analyzer` (internal_helper), `ai_supervisor` (management_agent), the six supervisor read tools (read_tool), `persona_runner`/`judge`/`prompt_improver`/`code_fixer` (testing_utility — registered so the permission model *covers* them; not callable from production flows). `architecture_agent` may exist only as `kind: production_specialist, installed: false` roadmap metadata — non-executable.

Execution check (controller, every invocation): registered capability ∧ caller role/mode ∧ project scope ∧ revision ∧ budget ∧ availability. **Registry presence alone is never authorization.** Each invocation snapshots the definition/config versions used. The supervisor and dashboard see an authorized summary only. A customer brief requesting an integration creates no registry entry (design.md §2.1).

Future-specialist recipe: implement adapter → declare contracts/capability → pass contract + policy tests → authorized registration/activation → consume an eligible approved handoff. No plugin framework, no discovery service.

## 5. Review, approval, and disposition records

### 5.1 ReviewRequest

```
{ review_id, source: finding|gap|proposal|validation_finding (ref),
  bound_versions: { spec_revision, material_manifest, policy },
  required_decision, context_summary, evidence_refs,
  assignee: role or identity | null, originator, created_ts,
  status: requested|assigned|acknowledged|in_review|resolved|declined|reassigned|superseded|cancelled,
  disposition?: { decision, reason, decided_by, ts } , resume_conditions }
```
Assignment names who *should* respond — it grants no permission and proves no acceptance (acknowledged is a distinct state). A dashboard notice is not delivery proof; V1 has **no** email/Slack notifications (none exist today; adding any is a separate approval). Requests may sit unassigned/unanswered: visible fallback is the Needs Attention view + alert A6 after a configured window; **silence never auto-approves**. Due dates/escalation timers are optional policy, never fabricated commitments; waiting on a reviewer is not a stuck run and triggers no LLM calls.

Distinct decisions with distinct authority: **acknowledge** (assignee), **accept a finding** (authorized reviewer), **correct a requirement** (business decision-maker — operator status alone does not confer factual authority over the business; locally one person may act in both roles, explicitly labeled, without inventing a customer confirmation), **approve a package** (operator with approve permission), **approve an executable action** (future mode only). Reasons + evidence stored on every decision.

Staleness: resolutions bind to the reviewed revision; if the spec changed first, the request goes `superseded` (or requires revalidation). Old acceptance never applies to new content; acknowledgment never clears a blocker; resuming work happens only through explicit controller transitions whose preconditions now pass. A merely-suspected AI issue stays advisory until the defined human/policy process accepts it; accepting one finding neither approves the package nor overrides hard gates.

### 5.2 Approval

```
{ approval_id, kind: package|action, target: rev_n | prop_id+digest,
  actor (identity+role), scope, expiry?, ts, reason }
```
Bound to exact revision/digest; controller revalidates at use (ai-supervisor.md §7).

### 5.3 Worked example (prompt §10)

Conflicting booking-confirmation statements (finding F-9, ai-supervisor.md ex. 2) → ReviewRequest RV-3 bound to r14, assigned role `business-decision-maker` → owner answers "staff confirms, bot proposes only" → disposition recorded with evidence (owner's message) → controller accepts a correction commit → r15 → readiness re-run → RV-3 `resolved`; package approval of r15 is its own later explicit action.

## 6. Materials contracts

**Manifest row**: `{ material_id, project_id, storage_path (server-assigned, safe), display_name (original, sanitized for rendering), content_hash (sha256), size, media_type, uploaded_ts, analysis: {status: pending|analyzed|failed, analyzer_version, analysis_ref}, authorization: accepted|disputed|withdrawn }`.

Rules: per-project directories (fixes shared `uploads/`, R2/R7); existing upload validation preserved (extension allowlist, basename-only names, 15MB, ≤20 files — `dashboard.py:1288-1314`) and re-enforced server-side in the new API; **content-hash change detection** replaces filename-only comparison (fixes R5) while preserving the observable behavior of `_scan_materials`'s three outcomes (analyzed/unchanged/empty) and the fail-loud gate (`requirements_bot.py:339-344`). Removal/replacement/partial failure/changed authorization each update the manifest and flag dependent facts (via Evidence links) for review. Uploaded text is untrusted evidence system-wide. Analysis usage is recorded per invocation; unchanged content is never re-sent. Raw customer materials stay out of general-purpose logs.

**Retention and withdrawal** (policy mechanics; ops doc §6 for the policy table): immutability applies to *metadata and hashes*; sensitive payloads are removable under an authorized action that leaves a tombstone (`availability: withdrawn`) — honest `evidence unavailable` everywhere it was cited, invalidation of dependent advisory results and future contexts, and no silent reuse through old summaries, caches, or imported analyses. What already left in exports cannot be recalled and the design says so. These are technical policies, not legal-compliance claims; retention periods are operator decisions flagged for qualified review, never invented. Real customer data is never copied into persona/improver datasets by default.

## 7. Operational records

### 7.1 Core tables

`projects, interview_sessions, management_conversations, messages(conversation_type!), runs(lease, state), invocations(usage, purpose, model, versions), spec_revisions(immutable, parent), rejected_candidates, materials, review_requests, approvals, supervisor_requests, supervisor_results, findings, finding_dispositions, action_proposals, action_decisions, action_receipts, alerts, events` + evaluation tables (ops doc §5). Consolidation of low-volume tables is an implementation freedom, provided the record shapes hold.

### 7.2 Event envelope and types

```
{ event_id, seq (monotonic per project), ts, project_id?, session_id?, run_id?,
  conversation_type?, actor: {kind: operator|owner|controller|reqbot|supervisor|system, role},
  type, payload (sanitized) }
```
Types: `message.received|shown`, `run.queued|claimed|committed|rejected|failed|cancelled|interrupted|outcome_unknown`, `invocation.started|finished|failed`, `materials.uploaded|analyzed|analysis_failed|changed|withdrawn`, `validation.finding`, `revision.committed`, `conflict.recorded|resolved`, `waiting.entered|exited`, `review.requested|assigned|acknowledged|resolved|superseded`, `approval.recorded|invalidated`, `export.created`, `supervisor.requested|started|completed|failed|skipped`, `finding.recorded|dispositioned`, `proposal.created|rejected|awaiting_approval|executed`, `recommendation.superseded`, `alert.opened|acknowledged|resolved|superseded`. Events separate advice/authorization/execution; **replay is read-only and can never cause a paid call**.

### 7.3 Transition tables (normative excerpts)

Project/interview (full machine in design.md §10):

| From | Action (actor) | Preconditions | Durable writes | Events | Failure route |
|---|---|---|---|---|---|
| interviewing | owner message (owner) | session open; no active run | message, run | message.received, run.queued | run fails → interviewing (project intact) |
| interviewing | commit turn (controller) | lease valid; parent = head | revision, snapshot, messages, run | revision.committed, run.committed | conflict → candidate archived, run.rejected |
| interviewing | bot signals complete (controller maps) | gates already consumed by bot | state=interview_closed | waiting.exited, run.committed | — |
| interview_closed | readiness eval (controller) | — | readiness result | review.requested* | error → conservative not-ready |
| review_required | approve (operator) | all blocking dispositions resolved; revision = evaluated revision | approval | approval.recorded | stale revision → refuse |
| approved | any accepted change | explicit action | new revision; approval invalidated | approval.invalidated | — |

Run: `queued→claimed` (lease acquire), `claimed→executing` (adapter start), `executing→validating→committed` (atomic), any→`cancelled` at safe boundary only, lease expiry→`interrupted`, provider ambiguity→`outcome_unknown`.

UI **log-pause** (the console's ⏸, a display freeze — `dashboard.py:826`) is distinct from workflow-pause (a project state); the design keeps both, labeled.

## 8. APIs

### 8.1 Legacy routes — preserved verbatim

`GET /data` (legacy training payload — **not repurposed**), `GET /chat/history`, `POST /chat/send`, `POST /chat/upload`, `POST /chat/reset` keep today's request/response shapes (`dashboard.py:1327-1363`), served by adapters bound to the **default project** (a real project row auto-created on first use). No cross-project leakage: legacy routes cannot name a project, so they only ever touch the default one. `/chat/*` remains the RequirementsBot interview contract — never a supervisor endpoint.

### 8.2 Namespaced API (`/api/v1/...`)

All POSTs accept `Idempotency-Key`; revision-conflicting writes return `409 {code:"revision_conflict", head:"rev_15"}`; scope violations `403 {code:"scope_denied"}`; cancellation via `POST .../cancel` honored at safe boundaries; errors are typed `{code, message, ref?}`.

```
Projects        GET/POST /projects ; GET /projects/{p}
Interview       GET  /projects/{p}/messages?after=<seq>
                POST /projects/{p}/messages          {message}           → run ref
                POST /projects/{p}/materials         (upload)            → manifest row
                GET  /projects/{p}/materials
Runs/state      GET  /projects/{p}/runs/{r} ; POST /projects/{p}/runs/{r}/cancel
Revisions       GET  /projects/{p}/revisions ; GET /projects/{p}/revisions/{rev}
Events          GET  /projects/{p}/events?after=<seq>
Conflicts       GET  /projects/{p}/conflicts
Readiness       GET  /projects/{p}/readiness         (computed, versioned rubric)
Review          GET  /projects/{p}/reviews ; POST /projects/{p}/reviews/{id}/assign|acknowledge|disposition
Approval        POST /projects/{p}/approvals         {kind, target_revision, reason}
Export          GET  /projects/{p}/export?format=legacy|extended
Management      POST /projects/{p}/management/messages   {text}          → supervisor request (advisory)
                GET  /projects/{p}/management/messages
Supervisor      POST /projects/{p}/reviews:supervisor    {revision}      → review request (explicit trigger)
                GET  /projects/{p}/supervisor/results/{id}
Findings        POST /projects/{p}/findings/{id}/disposition
Proposals       GET  /projects/{p}/proposals ; POST /projects/{p}/proposals/{id}/reject
Capabilities    GET  /capabilities                    (authorized summary + availability)
Attention       GET  /projects/{p}/attention          (reviews + alerts, one view)
Alerts          POST /alerts/{id}/acknowledge|resolve
Testing (separate namespace, separate permissions; the production supervisor has NO access):
                POST /api/v1/testing/candidates | /evaluations | /promotions   (ops doc §5)
Future (documented, NOT enabled in V1): POST /projects/{p}/proposals/{id}/approve  — executable-action approval; requires the bounded-delegation mode flag; absent flag → 404.
```

Example — send a message:
```
POST /api/v1/projects/proj_01/messages   {"message": "We open at 9"}
→ 202 {"run_id":"run_88","state":"queued"}
GET  /api/v1/projects/proj_01/events?after=1041
→ 200 {"events":[…run.committed, revision.committed rev_15…],"cursor":1049}
```

### 8.3 Handoff package (export `format=extended`)

```
{ "manifest": { project_id, approved_revision: "rev_15", revision_hash, schema_version,
                prompt_hash, generated_ts, approval_ref },
  "legacy_brief": { requirements, conversation_analysis },   ← byte-compatible with brief()
  "extended": { governance: {refs, evidence index, decisions, acceptance_criteria},
                unresolved: [refs into open_items/pending_design_decisions records],
                readable_summary: "…", readiness: {…counts, named gaps} } }
```
Open questions/decisions are **references** into canonical records, not editable copies. Only an *eligible approved* package may ever be handed to a future Architecture Agent through its registered contract; V1 neither implements nor invokes one. `format=legacy` returns the unchanged legacy shape alone.

## 9. Security and validation boundaries

- All validation server-side: project isolation on every query (WHERE project_id, enforced in `store.py` accessors), role checks per endpoint, request-size limits, upload rules (§6), secret redaction in events/payloads, HTML-escaping of all user/model text in every view (existing `esc()` pattern retained and extended).
- Injection surfaces reviewed in implementation: chat rendering, log console, finding/evidence display (all render untrusted text), upload names (basename-only already), the export download.
- CORS: same-origin only (no CORS headers), localhost binding preserved (`dashboard.py:1371`); shared access = separate auth design (open decision, decisions.md §4).
- Prohibited to both AI roles, enforced structurally (no such tool/endpoint exists for them): cross-project reads, shell, source/prompt writes, self-approval, permission changes, deployment, legacy improver/push pipeline.
- Untrusted-input rule: any LLM-provided path/ID/state/score/claim is validated against server records before use; structural validity ≠ semantic truth; human review remains in the loop for high-impact changes (design.md §11).
