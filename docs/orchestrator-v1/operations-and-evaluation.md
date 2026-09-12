# Operations and Evaluation — Orchestrator V1

Contracts referenced here are canonical in [contracts-and-schema.md](contracts-and-schema.md); metrics/alerts/promotion records defined there are not redefined elsewhere.

## 1. Factory outcomes vs customer outcomes

The customer's `success_criteria` describe the *future chatbot*. The factory's scorecard (§2) measures the *requirements process*. The two never mix: no factory metric enters `BusinessRequirements`, and no customer success criterion is treated as a factory KPI.

## 2. Factory scorecard

Each metric defines: purpose, calculation, source, sampling unit, inclusion/exclusion, slice/version, reviewer, uncertainty handling, and the decision it informs. Errors and incomplete interviews are **reported, never excluded to inflate averages**. A turn ≠ an invocation ≠ an interview ≠ an evaluated project — every metric names its unit.

| Metric | Calculation (unit) | Source | Informs | Uncertainty handling |
|---|---|---|---|---|
| **Fact preservation** | supplied-and-authorized facts retained with meaning/figures/provenance ÷ facts supplied, per evaluated interview | fixture fact sheets + adjudicated brief comparison | prompt/candidate promotion; regression | owner corrections and withdrawn evidence are excluded from the denominator, listed separately |
| **Unsupported assertions** | count of facts recorded/presented as confirmed without adequate evidence, per interview | evidence links + human adjudication | promotion; hard-gate review | missing evidence counts *against* confirmation, never for it |
| **Interview effort** | duplicate/unnecessary questions; human corrections needed post-interview, per interview | transcript review + review-record edits | prompt quality | necessary clarifications don't count; premature termination isn't rewarded |
| **Readiness/review quality** | meaningful gaps found vs missed; changes required before approval; authorized human minutes; abandonment; wrong approve/block decisions, per project | review records | rubric versioning; process changes | small samples reported as counts, not rates |
| **Supervisor value** | evidence-grounded status claims (adjudicated sample); accepted actionable findings; false alarms; missed issues; unsupported suggestions; behavior on unavailable evidence, per request | supervisor results + adjudication | supervisor enable/rollback | reviewer acceptance alone ≠ correctness — adjudicated samples required |
| **Reliability** | contract violations; recoveries; duplicate committed effects; isolation failures; stale-result rejections (correct = good); unauthorized action attempts vs accepted, per run | events | hard invariants | any nonzero committed-duplicate or leakage is a release blocker |
| **Efficiency** | measured active processing time; human-wait separately; per-interview and per-review cost including **all** attempts; incomplete/unknown accounting shown | invocations | budgets, model choice | unknown usage displayed as unknown, never zero |

### 2.1 Three attributable comparisons

- **A. Baseline**: existing RequirementsBot exactly as-is.
- **B. A + controller/adapters, supervisor disabled** — isolates orchestration overhead and safety (target: behavioral equivalence at the contract boundary; mock replay proves control-flow equivalence, not stochastic-output identity).
- **C. B + on-demand advisory supervisor** — isolates supervisor value at its measured added cost/latency (never fabricated from mocks).

Bot prompt/model/settings stay pinned across A/B/C when isolating B or C. Like-for-like inputs first (fixtures); adaptive live outcomes evaluated separately, only when authorized. Prompt improvements and supervisor introduction are different changes — never credited to each other.

### 2.2 Pilot

Representative sanitized cases: FAQ-only business, scheduling, payment-policy capture (no live transactions), multilingual/typo-heavy owner, uploaded materials, refusal/early departure, genuine and false contradictions, restart/concurrency faults. Opt-in activation per project with rollback; no silent transcript-sharing with additional models; no paid A/B without authorization. A small pilot yields preliminary evidence, not universal reliability.

### 2.3 Release gates

Hard invariants (every phase): no unauthorized committed changes (prompt/code/push), no cross-project data leakage, no promotion of untested candidate versions, byte-compatible legacy exports, honest completion state (closed ≠ ready ≠ approved). Quality thresholds require an agreed measured baseline and authorized targets — no invented universal score, no 90% completeness gate. Insufficient evidence is marked insufficient, never rounded up to success. Passing a test set is not a zero-future-risk claim.

## 3. Usage, cost, and time accounting

Every invocation is accounted: interview turns, materials analyses, supervisor requests, retries, invalid responses, failed runs. Counters preserved from the bot (`fresh_in/cache_read/cache_write/out`). Aggregation is from invocation rows only (no nested/cumulative double counting; shared evaluation costs attributed once, to the testing scope). Supervisor usage: separate visible breakdown + included once in the project total. Pricing from versioned `config/pricing.yaml` (seeded from the constants at `parallel_personas.py:38-43`, explicitly marked "configuration as of 2026-06, not verified current"); measured tokens vs estimated cost vs unknown are three displayed states. Unknown model ⇒ unknown cost (fixes R8). Time: active execution vs waiting-for-human vs evaluation, separately; project-work metrics never mixed with testing metrics.

## 4. Operational alerts

A small **deterministic** policy (`alerts.py` + `policies.yaml`) over existing events/records — not an AI monitor, no continuous paid checks, no external notifications in V1 (in-dashboard only, in Needs Attention).

Alert conditions:

| ID | Condition | Notes |
|---|---|---|
| A1 | Repeated schema/parse failures (same session, windowed count ≥ threshold) | bot `_ask` retries surface here when a whole run fails |
| A2 | Actively executing run exceeds configured deadline OR loses its worker lease | *executing* only — queue delay, human waiting, and supervisor unavailability are distinct, non-alerting states with their own display |
| A3 | Invocation with missing/unknown usage or `outcome_unknown` run | honest-accounting alarm |
| A4 | Approaching an approved resource budget (configurable %) | scope: run / project / testing |
| A5 | Registered dependency revoked/unavailable (config missing, availability stale on an *enabled* capability) | |
| A6 | ReviewRequest needing a decision-maker with none assigned past a configured window | window is policy, not a fabricated commitment |

Alert record: rule/policy version, scope, triggering record, window/sample count, threshold source, severity, owner, dedup key, first/last seen, count, status (`open|acknowledged|resolved|superseded`), evidence links, allowed response, resolution condition. **Acknowledgment ≠ resolution.** Dedup + bounded windows/cooldowns keep polling from flooding the operator.

Thresholds are configurable and either operator-approved or explicitly `provisional` until a baseline exists. No invented "proven" limits; one poor review is never labeled "model drift" — quality-trend warnings require comparable evaluated versions, sufficient observations, known dispositions, and must distinguish a model change vs a changed test population vs ordinary variance (else: `inconclusive`).

An alert grants no authority: no auto-retry, no source repair, no prompt change, no budget raise, no deploy. Any protective pause is a separately specified, explicitly enabled deterministic policy with defined scope and recovery. A failed optional supervisor request never suspends a healthy interview.

## 5. Testing relocation, candidate evaluation, and controlled promotion

### 5.1 What changes and why (snapshot findings)

Confirmed in the live repo (same code as the snapshot): the default persona-run path evaluates interviews on the *current* prompt, then rewrites `interviewer_prompt.txt` in place (`parallel_personas.py:548`), commits with the **pre-rewrite average score in the message** (`:583-584`), pushes to `origin main` (`:586`), then lets a headless agent edit code and push (`:611-645`). The rewritten prompt is never itself evaluated; the `_violation` guardrails (`:504-513`) check placeholders/length/identity only. Documented as the existing behavior; the proposed permission change below is an explicit, approval-required change (preservation doc §4.1).

### 5.2 Preserved

Persona generation, interviews, judging, historical logs/scores, findings, prompt-improvement tooling and code suggestions all survive under Testing & Improvement (dashboard.md §8). Historical `parallel_runs/` results import as historical observations with `version: unknown` where metadata is absent; past rewritten prompts are **not** retro-labeled evaluated winners. None of these tools run during a customer interview or supervisor session; the supervisor has no tool that can start them — it may only file an advisory issue.

### 5.3 The promotion pipeline (replaces improve-and-push)

```
record/evaluate exact baseline
  → generate ISOLATED candidate (artifact/workspace — never the active interviewer_prompt.txt)
  → candidate validation + candidate-specific evaluation runs
  → baseline-vs-candidate comparison (dimension-level, §5.5)
  → authorized release decision (human)
  → controlled activation (new sessions only; ongoing sessions stay pinned)
  → observe; nondestructive rollback available
```

Candidate creation writes `candidates/<hash>/prompt.txt` (+ metadata). The active prompt is never mutated to test a candidate. Per-session pinning (design.md §9) means candidate evaluation runs pass their prompt per-instance — no global class-level `PROMPT_FILE` change that would touch concurrent interviews. No running interview ever changes configuration because a promotion happened.

Three explicitly separated activities with separate authorization: **evaluation-only** (measure, no writes), **candidate generation** (produces an artifact), **promotion/merge approval** (human, recorded). Human-approved promotion is still not permission to commit/push or deploy — repository and deployment writes are separately authorized actions.

### 5.4 Evaluation and promotion records

**EvaluationRun**: evaluated candidate hash/version + configuration, baseline hash/version, dataset/fixture version, rubric/judge version, run IDs, execution evidence, failures, usage, measured latency, missing/inconclusive results. A score label without execution evidence is neither an execution nor a promotion receipt; the baseline's score is never attached to a candidate as its measured score.

**PromotionDecision + ReleaseManifest**: exact candidate ↔ its evaluation evidence, approving actor, risk assessment, target environment, pre-promotion active version, rollback reference. Eligibility requires: hard invariants (§2.3) pass, version-specific acceptance criteria met, authorized approval, and the active version unchanged since comparison (else re-evaluate the material difference). Failed/stale/incompatible/insufficient evaluation ⇒ `not_eligible`/`inconclusive` — never auto-pass.

**Combined releases**: code or schema changes invalidate evaluations of a different configuration. When prompt+code+schema change together, the exact combination is evaluated; a prompt-only pass never approves later code fixes, and an improvement chain cannot evaluate a prompt, alter code, then promote the untested combination. A candidate also cannot qualify by editing its own tests or grading policy — fixture/rubric versions are pinned in the EvaluationRun and diffs against them are part of eligibility.

The previous release is retained with a nondestructive rollback path.

### 5.5 Datasets, judges, and measurement discipline

Three separated case pools: **development** cases (visible to the improver), **fixed regression** cases, **held-back acceptance** cases (never shown to the improver). Freezing a persona means freezing the fact sheet, supplied materials, simulator settings, and the observable transcript — a name alone is insufficient. Hidden persona facts stay hidden from the bot under test; capture-of-supplied-facts and opportunity-to-ask-about-undisclosed-facts are measured separately (an undisclosed fact is not automatically a missed requirement). Human-approved owner statements outrank simulated or judged assumptions.

Mocks test control flow and safety only. Live evaluations require separate explicit authorization and a budget; when authorized, compare dimensions — preserved facts, unsupported assertions, false/missed contradictions, repetition, closure behavior, schema validity, human edits, latency, total usage — not one aggregate. Comparable configurations and cases; nondeterminism documented (repeats, sample limits). AI-judge findings are calibrated against human-adjudicated evidence and include no-conflict/negative cases to measure false positives.

Memory/logging is not model training; candidate generation is not improvement until the evidence above supports it.

## 6. Retention, evidence withdrawal, and safe context

Documented policy per record type (defaults; retention *periods* are operator decisions flagged for qualified review — none invented here, and nothing in this section is a legal-compliance declaration):

| Record type | Default policy |
|---|---|
| Uploaded materials | Retained per project; payload removable by authorized action → tombstone + `withdrawn` evidence status |
| Raw model-facing conversation (bot `chat_history`) | Retained (needed for resume); access restricted to operator debugging views; excluded from exports |
| Clean interview messages | Retained; part of the project record |
| Supervisor conversations/results | Retained, project-scoped |
| Spec revisions | Immutable metadata + hashes always; sensitive payload redaction possible with tombstones |
| Usage/invocations, events | Retained (audit) |
| Evaluation fixtures | Sanitized/synthetic only by default — real customer data is never copied into persona/improver datasets by default |

"One authoritative store" ≠ retain every raw payload forever or show it to every role. Withdrawal semantics: honest `evidence unavailable` wherever cited; dependent advisory results and future contexts invalidated; no silent reuse via old summaries, caches, or imported analyses. Already-exported copies cannot be revoked and the UI says so plainly.

## 7. Incident and rollback procedures

- **Bad promotion**: rollback via ReleaseManifest reference; new sessions revert; pinned ongoing sessions were never affected; incident note links the EvaluationRun that misled.
- **Store corruption**: SQLite file backup rotation (on daily first-write and before every schema migration); restore = replace file, replay nothing (events are in the file).
- **Provider outage**: runs fail with typed errors; interviews resume when the operator retries; no auto-retry storms (design.md §10).
- **Unknown provider outcome** (crash mid-call): run `outcome_unknown`, alert A3, explicit-only re-run (design.md §7.2).
- **Legacy pipeline accidentally invoked** (pre-migration): the phase-1 guard (migration doc, phase G0) makes the default path evaluation-only; invoking mutation requires an explicit flag whose use is itself logged.
