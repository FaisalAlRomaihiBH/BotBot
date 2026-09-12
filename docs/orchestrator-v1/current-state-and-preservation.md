# Current State and Preservation — Orchestrator V1

Status: design document (no application changes made). Evidence cites the live repository at commit `6034853` (2026-09-13). HANDOFF_FOR_AI.md was generated from this same commit on 2026-09-12 and matches the live code; the only repository difference is an untracked `uploads/` directory (owner materials, not code). Claims below marked **[snapshot]** were verified only against this state and must be re-checked if the repo moves.

## 1. Inventory: what is actually implemented

### 1.1 Interview engine

| Capability | Evidence | Status |
|---|---|---|
| Interactive CLI interview, saves complete brief | `main.py:12-48` | Implemented |
| Partial-brief export on early exit (`"partial": true`) | `main.py:51-56`; also `dashboard.py:1317-1324` (`chat_reset` saves a partial brief) | Implemented |
| Full requirements schema (~70 fields + 8 submodels) | `models.py:223-573` (`BusinessRequirements`), `models.py:20-221` (submodels) | Implemented |
| String-or-object unions for back-compat | `models.py:252,260,324,330,393,533` (`Union[X, str]`) | Implemented |
| Explicit unknown sentinels | `models.py:10-17` (`UNKNOWN_VARIES`, `UNKNOWN_PENDING`) | Implemented |
| Full-form JSON output every turn | `models.py:593-598` (`InterviewTurn`); `requirements_bot.py:203-206` (max_tokens=16000 because the form grows) | Implemented |
| `send()` returning `(messages_to_show, final_turn)`, 2 messages on file analysis | `requirements_bot.py:232-287` | Implemented |
| Materials analysis (text + images → `ConversationAnalysis`) | `requirements_bot.py:31-154` (`MaterialsAnalyzer`) | Implemented |
| Auto-scan of `uploads/` before every turn; filename-set change detection | `requirements_bot.py:240-241,326-348` (`_scan_materials`) | Implemented |
| Fail-loud materials gate (refuse to interview blind) | `requirements_bot.py:339-344` and `requirements_bot.py:574-579` | Implemented |
| One-time essentials sweep gate (budget/timeline/scope) | `requirements_bot.py:260-268`; `_missing_essentials` at `requirements_bot.py:847-861` | Implemented |
| One-time deferred-promise nudge gate | `requirements_bot.py:276-284`; `_outstanding_commitments` at `requirements_bot.py:828-844` | Implemented |
| Completion refused until `problem_to_solve` and `channels` filled | `requirements_bot.py:612-614` | Implemented |
| Deterministic post-processing (`_postprocess`) | `requirements_bot.py:350-544` | Implemented |
| — upload-fact provenance tags + verified-flag merge | `requirements_bot.py:371-374,892-920`; tags at `requirements_bot.py:631-638` | Implemented |
| — customer-replies-owed reconciliation | `requirements_bot.py:377-386,864-889` | Implemented |
| — legacy `owner_sentiment_or_concerns` pointer list | `requirements_bot.py:396-409` | Implemented |
| — turnaround / conditional-price gap generation | `requirements_bot.py:418-482` | Implemented |
| — budget-figure gap | `requirements_bot.py:500-506` | Implemented |
| — `open_items` rebuilt as reference index | `requirements_bot.py:508-544` | Implemented |
| Prompt caching (system + moving history breakpoint) | `requirements_bot.py:546-566` (`_build_messages`) | Implemented |
| Prompt file re-read fresh each turn (live reload) | `requirements_bot.py:550-552` | Implemented — see risk R6 |
| Retry with format-error feedback (3 attempts) | `requirements_bot.py:568-619` (`_ask`) | Implemented |
| Token accounting (fresh/cache-read/cache-write/out) | `requirements_bot.py:227-229,595-599` | Implemented |
| State (de)serialization for cross-process sessions | `requirements_bot.py:298-323` (`to_dict`/`from_dict`) | Implemented |
| Persisted manual persona sessions | `persona_test.py:29-92` (`PersonaSession`) | Implemented |

### 1.2 Testing and self-improvement pipeline

| Capability | Evidence | Status |
|---|---|---|
| Batch persona generation (10 per call, retries) | `parallel_personas.py:234-266` | Implemented |
| Parallel interviews, per-interview isolated uploads dir | `parallel_personas.py:268-338` (`run_interview`; `uploads_{idx:03d}` at 271-274) | Implemented |
| Judge scoring (5 dims, findings with verbatim excerpts, code_suggestions) | `parallel_personas.py:148-204,340-354` | Implemented |
| Run artifacts + summary.json + latest.txt pointer | `parallel_personas.py:226-231,401-403,451-452` | Implemented |
| Cost model (per-model pricing, cache multipliers) | `parallel_personas.py:38-57` — **configuration constants, not verified current prices** | Implemented |
| PromptImprover: rewrites live `interviewer_prompt.txt` | `parallel_personas.py:459-556` | Implemented — see risk R1 |
| `improve_and_push`: git add/commit/push to `origin main` | `parallel_personas.py:565-586` | Implemented — see risk R1 |
| `fix_code_issues`: headless `claude -p` agent edits code, commits, pushes | `parallel_personas.py:589-645` | Implemented — see risk R1 |
| Safety ceiling 60 turns (circuit breaker) | `parallel_personas.py:34,281` | Implemented |

### 1.3 Dashboard

| Capability | Evidence | Status |
|---|---|---|
| ThreadingHTTPServer on 127.0.0.1:8500, single embedded page | `dashboard.py:1369-1371,420` (`PAGE`) | Implemented |
| Routes: GET `/data`, GET `/chat/history`, POST `/chat/send`, `/chat/upload`, `/chat/reset`; any other GET serves the page | `dashboard.py:1327-1363` | Implemented |
| Run-state derived by regex-parsing `run.log` | `dashboard.py:20-21,57-184` (`IO_RE`, `collect`) | Implemented — see risk R4 |
| 5-stage pipeline view + per-persona lanes | `dashboard.py:194-377` (`build_lanes`, `build_stages`) | Implemented |
| Top summary bar (cost, tokens, elapsed, state, running/failed/completed) | `dashboard.py:762-772` | Implemented |
| Live Logs console: filters, search, auto-scroll, pause, copy, clear, fullscreen | `dashboard.py:813-832` | Implemented |
| Stage inspector panel | `dashboard.py:849-854` | Implemented |
| Chat page: history, send, uploads (attach + drag/drop), reset, Enter/Shift+Enter | `dashboard.py:834-846,1094-1166,1224-1324` | Implemented |
| Upload validation: extension allowlist, path-strip, 15MB cap, 20-file cap | `dashboard.py:1288-1314` | Implemented |
| Chat state: single global `CHAT` object + lock, one interview at a time | `dashboard.py:1224-1236` | Implemented — see risk R2 |
| Clean-message vs raw-JSON separation (`CHAT["shown"]` vs `bot.chat_history`) | `dashboard.py:1250-1258` | Implemented |
| Materials-gate error surfaced in-thread, not a 500 | `dashboard.py:1277-1281` | Implemented |
| 2-second polling | `dashboard.py:1170` (fetch `/data`) | Implemented |

### 1.4 UI placeholders (not implemented capabilities)

| Placeholder | Evidence |
|---|---|
| Sidebar items Personas, Interviews, Evaluations, Fixes, Reports, Settings — no `data-view`, no click handler | `dashboard.py:732-737` vs. `dashboard.py:1067-1069` (only `[data-view]` items are wired) |
| Run-header buttons Pause ⏸, Stop ■, Restart ↻, Settings ⚙, More ⋯ — rendered, no handlers | `dashboard.py:753-759` |
| Sidebar footer "API connected", "Claude operational" green dots — hardcoded, never measured | `dashboard.py:739-743` |
| Five of six cycle tiles (Builder Bot, Brief Quality, End-Customer, Live Feedback, Regression) — explicit "Not built yet" panels | `dashboard.py:780-811` |
| "Claude Membership" usage label on the codefix stage | `dashboard.py:302-303` — a label, not a billing fact |

### 1.5 What does NOT exist (confirmed by inspection)

- No orchestrator, controller, contracts, registry, or adapter code anywhere in the repo (only the seven Python/txt files listed above plus `requirements.txt`).
- No database. State lives in JSON files (`requirements_brief.json`, `persona_sessions/*.json`, `parallel_runs/*`) and process memory.
- No React/Next.js/FastAPI/LangGraph/Redis/PostgreSQL. `requirements.txt` pins langchain, langchain-anthropic, pydantic, python-dotenv only **[snapshot]**.
- No `docs/` directory before this design set.
- No authentication anywhere; server binds 127.0.0.1 only (`dashboard.py:1371`).
- No supervisor of any kind; the word "orchestrator" appears nowhere in the source.

## 2. Observed defects and risks (preserve function ≠ preserve these)

- **R1 — Unguarded production mutation.** A persona run, by default (`--no-improve` absent), rewrites the live `interviewer_prompt.txt` (`parallel_personas.py:548`), commits and pushes to `origin main` (`:581-586`), then hands code edits to a headless agent allowed `git push` (`:632`). The rewritten prompt is **never evaluated before promotion**: the commit message carries the *previous* prompt's average score (`improve_and_push` formats `summary['avg_score']` — the score of interviews run on the *old* prompt — into the commit for the *new* prompt, `:583-584`). Guardrails check only placeholders/length/identity (`_violation`, `:504-513`), not behavior. Design response: §17 of the design prompt; candidate isolation and promotion gates in operations-and-evaluation.md.
- **R2 — Single shared chat session.** One global `CHAT` and one shared `uploads/` dir (`dashboard.py:1224,1294`): a second concurrent interview or project silently shares materials and state. Design response: per-project isolation, design.md §6.
- **R3 — Misleading status displays.** Hardcoded green "API connected / Claude operational" dots and dead Pause/Stop/Restart buttons (§1.4). Design response: dashboard.md — observed health or visibly disabled.
- **R4 — State from log regex.** Orchestration truth derived by regexing `run.log` (`dashboard.py:84-177`) breaks silently when a log line's wording changes. Design response: structured durable events (design.md §8); the legacy parser is retained for historical runs only.
- **R5 — Content-blind materials change detection.** `_scan_materials` compares filename lists only (`requirements_bot.py:333-336`): replacing a file's bytes under the same name silently reuses the stale analysis. Design response: content hashes in the materials manifest (contracts-and-schema.md §6).
- **R6 — Live prompt reload is global and mid-interview.** The prompt file is re-read every turn (`requirements_bot.py:550-552`), so an improver rewrite (R1) or manual edit changes a *running* interview's behavior mid-conversation, for *all* concurrent bots (the persona runner runs many `RequirementsBot`s against the same `PROMPT_FILE` class attribute, `requirements_bot.py:165`). Design response: per-session pinned prompt versions (design.md §9; operations-and-evaluation.md §5).
- **R7 — `/chat/upload` writes to a fixed shared directory** with an extension allowlist but no per-project scoping (`dashboard.py:1294`). Covered by R2's isolation design.
- **R8 — Unknown-model pricing silently defaults to Opus rates** (`parallel_personas.py:48`). Design response: versioned pricing config; unknown model ⇒ cost displayed as unknown, never silently substituted (operations-and-evaluation.md §3).
- **R9 — `requirements_brief.json` is a single shared filename** written by both `main.py` and the dashboard chat (`main.py:45-47`, `dashboard.py:1246-1247`); two projects overwrite each other. Design response: per-project exports plus a compatibility copy (design.md §6).

None of these defects may be *reproduced* as "preserved behavior"; each is preserved-in-function, corrected-in-mechanism, and every user-visible change is listed in §4.

## 3. Preservation matrix

Columns: existing capability → files/functions → proposed integration → regression checks (IDs reference migration-and-tests.md §2) → intentional changes.

| # | Existing capability | Where | Proposed integration | Regression checks | Intentional changes |
|---|---|---|---|---|---|
| P1 | `BusinessRequirements` schema: names, types, nullable meanings, unions, sentinels | `models.py` | Untouched; orchestration metadata lives in a separate envelope (contracts-and-schema.md §3) | T1, T5 | None |
| P2 | `InterviewTurn` full-form-per-turn contract | `models.py:593-598`, `requirements_bot.py` | Untouched; adapter derives diffs from before/after snapshots, never patches | T2, T6 | None |
| P3 | `send()` → `(messages, turn)`, incl. 2-message analysis turns | `requirements_bot.py:232-287` | Wrapped by RequirementsBotAdapter; return shape preserved | T2 | None |
| P4 | `brief()`, `to_dict()`/`from_dict()`, `GREETING`, CLI flows, saved sessions | `requirements_bot.py:289-323`, `main.py`, `persona_test.py` | Untouched; import path for old sessions (design.md §6.4) | T1, T2 | None |
| P5 | Materials pipeline: auto-scan, unchanged/empty notes, fail-loud gate | `requirements_bot.py:31-154,240-251,326-348,574-579` | Preserved; scan gains content-hash comparison via manifest | T3 | Byte-changed same-name file now triggers re-analysis (fixes R5) |
| P6 | One-time essentials sweep + deferred nudge; flags survive resume | `requirements_bot.py:219-226,260-284,318-321` | Untouched | T4 | None |
| P7 | All `_postprocess` behaviors (dedupe, tags, gaps, index) | `requirements_bot.py:350-544` | Untouched | T5 | None |
| P8 | Prompt caching + usage breakdown | `requirements_bot.py:546-566,595-599` | Untouched; usage additionally journaled per invocation | T8 | None |
| P9 | Partial exports (CLI exit, chat reset) | `main.py:51-56`, `dashboard.py:1317-1324` | Preserved; also written per-project | T1 | Additionally saved per project (fixes R9); legacy filename still written for the default project |
| P10 | Interview completion ≠ readiness ≠ approval (graceful goodbyes) | `requirements_bot.py:260-287,612-614`, prompt WRAP-UP section | Controller adds review/approval states *after* interview-closed; never re-opens | T4, T14 | New explicit review/approval lifecycle (additive) |
| P11 | Chat UI: history, greeting, typing/error display, Enter/Shift+Enter, uploads, drag/drop, reset, exports | `dashboard.py:834-846,1094-1166,1250-1324` | Preserved as the RequirementsBot Chat view under a project selector | T9 | Chat is project-scoped; shared-global CHAT replaced (fixes R2) |
| P12 | Log console controls (filters/search/scroll/pause/copy/clear/fullscreen) + inspectors | `dashboard.py:813-832,849-854` | Preserved; new source filters added (Controller, Supervisor, …) | T9 | Clear affects the view only (already true; made explicit) |
| P13 | Top-bar metrics incl. "completed" at far right | `dashboard.py:762-772` | Preserved; values sourced from durable events; scope labeled | T9 | Unknown/estimated values labeled (fixes R8 display) |
| P14 | Legacy training displays (5-stage pipeline, lanes, run.log parser) | `dashboard.py:57-418` | Moved intact under Testing & Improvement; run.log parser retained for historical runs | T9 | No longer the default Home |
| P15 | Persona/judge machinery as testing tools | `parallel_personas.py` | Retained, relocated behind Testing & Improvement; **auto prompt-write + push removed from the default path** (design prompt §17) | T10 | Promotion becomes explicit, evaluated, and gated (fixes R1) — approval-sensitive change |
| P16 | Legacy routes `/data`, `/chat/*` request/response shapes | `dashboard.py:1327-1363` | Preserved verbatim, served by adapters against the default project | T9 | `/data` keeps its legacy training payload; not repurposed |
| P17 | Localhost-only binding, no auth | `dashboard.py:1371` | Preserved as the default deployment posture | T15 | Explicit roles recorded even when one local person plays both |
| P18 | Existing conversational style rules | `interviewer_prompt.txt` (all sections) | Untouched; supervisor prompt is a separate document | T2 | None |

## 4. Intentional behavior changes requiring approval

1. **Persona-run auto-mutation disabled by default** (R1): the improver writes an isolated candidate artifact; commit/push to `main` requires an explicit, recorded promotion decision. The `--no-improve` measure-only mode becomes the default posture of the orchestrated pipeline.
2. **Per-project session/material isolation** (R2, R7, R9): dashboard chat binds to a selected project; the legacy single-session behavior maps to a "default project".
3. **Content-hash materials change detection** (R5): stale-analysis reuse after byte-level file change disappears.
4. **Honest status UI** (R3): decorative indicators replaced by observed values or visibly-disabled controls.
5. **Pinned prompt versions per orchestrated session** (R6): live-reload survives only in an explicitly-marked development mode; an orchestrated interview keeps the prompt hash it started with.
6. **Cost display for unknown models shows unknown** (R8), never a silently substituted rate.

Everything else preserves observed behavior byte-for-byte at the contract boundary.
