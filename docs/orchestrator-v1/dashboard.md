# Dashboard — Orchestrator V1

Reuses the current dark visual language wholesale: the CSS variables (`--bg:#0d0d0d`, `--panel`, `--accent:#6ea8fe` etc., `dashboard.py:424-430`), typography, spacing, sidebar, badges, console, and inspector patterns. No bright-theme replacement, no frontend rewrite to draw a graph — the Home graph is lightweight SVG/DOM in the same embedded-page style. No image mockups are provided; behavior is specified textually and diagrams are Mermaid.

## 1. Navigation

Sidebar (replacing today's list at `dashboard.py:728-737`):

```
Home                        ← new default view (Orchestrator graph)
Requirements Bot Chat        (preserved chat view, project-scoped)
Contracts & Review           (revisions, readiness, reviews, approvals, exports)
Activity / Live Logs         (preserved console + new source filters)
Testing & Improvement        (relocated legacy training UI + candidate/promotion controls)
```

- **Project selector** in the header: every view is scoped to one selected project; no mixing projects in one chat or graph. Legacy single-session behavior maps to the auto-created default project.
- AI Supervisor Chat opens from the central circle (§3); an extra nav shortcut is optional and must open the *same* conversation, never a duplicate.
- Assumed local single-operator use (the server binds 127.0.0.1, `dashboard.py:1371`); shared/customer access is flagged as an unresolved product decision (decisions.md §4). Operator vs business-owner roles stay explicit even when one person tests both: the chat view acts as owner, everything else as operator, and records are labeled accordingly.
- Placeholder nav items that do nothing today (Personas, Interviews, Evaluations, Fixes, Reports, Settings — `dashboard.py:732-737`) are removed or folded into Testing & Improvement as real screens; none remain as dead entries.

## 2. Home: the central Orchestrator graph

Layout:
- **One large central circle labeled "Orchestrator"** representing BOTH the AI supervisor and the execution controller — never a second competing central circle, never a supervisor satellite. Diameter ≈ 2–3× a bot node's.
- **Smaller bot circles** placed radially, connected to the center with lines. Placement is **registry-driven** (reads `GET /capabilities`): V1 renders exactly one operational production specialist — RequirementsBot. Future bots appear only via an optional roadmap toggle, rendered unmistakably as `Planned — not implemented` (dashed outline, muted, non-clickable for execution). **No invented bots to fill the circle.**
- Responsive spacing, no overlapping labels; fit-to-view button; collapses to a vertical list under the existing 760px breakpoint (`dashboard.py:717-722`).

Node content (each *actual* node): identity, real state, current activity when present, most recent outcome. **Availability ≠ activity**: `Idle` is a healthy state, not absence. Center/edge/node animation runs only during relevant real backend execution (driven by events, never decorative), with `prefers-reduced-motion` honored (static state dots instead).

Edges: clicking an edge inspects the agent contract (`reqbot-io@1`) and the latest validated handoff. Caption clarifies: a line is **controller-mediated communication**, not authority for one AI to call another.

## 3. The Orchestrator panel (click the central circle)

Project-scoped panel with four tabs:

1. **Execution** — lifecycle state, actual work (runs, current invocation purpose), policy/contract versions in force, named blockers from readiness, approvals, recent events.
2. **AI Supervisor Chat** — the operator's management conversation. Visually and structurally distinct from the owner interview: different header, actor labels ("Operator" / "Supervisor"), its own input box; messages persist in `management_conversations` (contracts doc §7.1). Sending a message is the explicit paid trigger (a small cost note appears on the send button when advisory mode is on).
3. **Recommendations** — findings and proposals with supporting records: evidence links, source revision, staleness badge, disposition state, and *actual* action status (proposed / rejected / pending approval / …). **V1 advisory rows carry no execute or auto-approve buttons** — the only actions are disposition (accept/dismiss/defer) and reject-proposal. A future approval control, when the delegation phase lands, binds to one exact eligible action + revision.
4. **Needs Attention** — unified list of ReviewRequests and open Alerts (distinct types kept visually distinct), each showing cause, actual scope, owner/status, evidence links, and the permitted next action. Clicking opens the source record.

Status is shown as **two separate indicators**: controller health (process-observed: event flow, store writable) and supervisor mode/activity (`Disabled | Available/Idle | Reviewing/Answering | Unavailable/Error`) from real request records. The UI never shows "thinking" while only the controller or RequirementsBot is running.

## 4. RequirementsBot node → the preserved chat

Clicking the RequirementsBot circle opens its inspector (identity, contract, pinned prompt hash, session state) with a button into **Requirements Bot Chat** — the existing customer interview view, preserved: history with greeting, typing indicator and error display, Enter vs Shift+Enter (`dashboard.py:843`), attach + drag/drop uploads, "New interview" reset (which still saves a partial brief, `dashboard.py:1317-1324`), and exports. Additions that must not degrade chat usability: a slim side rail showing live spec-revision number and open-review count, each linking into Contracts & Review. Operator chat and owner interview remain separate transcripts, separate input boxes, separate visual identities — a management question can never land in the owner conversation or vice versa.

## 5. Honest state rules

- The hardcoded footer dots ("API connected", "Claude operational", `dashboard.py:739-743`) are replaced by observed values: store writable, events flowing, last provider call outcome + age. Unknown shows as unknown (gray), never green. **No paid health probes** exist just to color a dot.
- Dead header buttons (Pause/Stop/Restart/Settings/More, `dashboard.py:753-759`) are either wired to defined controller commands (workflow pause/cancel at safe boundaries — distinct from the console's display-pause) or rendered visibly disabled with a tooltip explaining why. No decorative controls anywhere.
- Polling/staleness: the page shows "last updated Ns ago" and a visible stale banner when polling fails (design.md §8) instead of silently freezing on old data. Reconnection is automatic; the banner clears on success.
- Supervisor output shows evidence links and its short decision summaries — no fabricated live "reasoning trace", no hidden chain-of-thought rendering.
- Metrics bar (preserved layout, `dashboard.py:762-772`): Total Cost, Input Tokens, Output Tokens, Elapsed, Run State, Active Runs, Failed Runs, **Completed Runs at the far right**. Values come from durable events/invocations; each value labels its scope (this project / this run / testing) and unknown or estimated values are labeled `~` / `unknown` — never a silent zero (fixes R8 display). Active-work time and human-wait time are separate; a failed optional supervisor request never shows as a failed interview. No duplicate current-stage/progress strips, and a successful turn is never presented as approved requirements.

## 6. Activity / Live Logs

The existing console is preserved: filter chips, search, auto-scroll, display-pause ⏸ (labeled "display only — work continues"), copy, clear-view (clears the *view*; audit history untouched), fullscreen, and inspectors (`dashboard.py:813-832`). Changes:

- New source filters: `Controller`, `AI Supervisor`, `RequirementsBot`, `Materials`, `Contracts/Approvals`, `Errors` — sourced from structured events. Legacy filters (Persona/Interview/Judge/Improver/Fixing Code) remain scoped to Testing & Improvement's log view.
- Three explicit line classes, visually distinct: user-visible messages, model JSON/system payloads (collapsed by default), internal diagnostics. All rendered escaped.

## 7. Contracts & Review

Revision list (immutable history with diffs), readiness report (applicable/covered/unknown/not-applicable counts + named gaps; the rubric version shown), review queue (assign/acknowledge/disposition per contracts doc §5), approval action (binds to the exact revision; disabled with a reason while blocking dispositions remain), export buttons (legacy / extended package). Interview-closed vs ready vs approved are three labeled stages on this screen — never conflated.

## 8. Testing & Improvement

The Learning & Training Cycles content moves here intact: the 5-stage pipeline view, per-persona lanes, legacy `run.log` parser for historical runs, and the six cycle tiles — the five unbuilt ones keep their explicit "Not built yet" panels (`dashboard.py:806-811`) and gain a `Planned` badge. New, per the promotion design (ops doc §5): candidate list with per-candidate evaluation evidence, baseline-vs-candidate comparison, and an explicit promotion action gated on eligibility. **No button on this screen (or any screen) invokes the legacy improve-and-push path**; until the promotion phase lands, the legacy pipeline stays reachable only from the CLI it lives in today, and the new UI exposes evaluation-only runs (`--no-improve` semantics).

## 9. Accessibility and responsiveness

Keyboard: all nodes/controls tabbable in a defined order; Enter/Space activates; visible focus rings (`:focus-visible` outline in `--accent`); Esc closes panels/inspector. Every state conveyed by color also has a text label (the existing badge pattern already pairs dot+text, `dashboard.py:860-863`). Graph nodes carry `role="button"` + `aria-label` with name and state. Mobile (<760px): sidebar collapses (existing behavior), graph becomes a stacked list, panels go full-screen. Empty/error/disabled states specified per view: no projects yet (create prompt), no events (quiet state, not fake activity), supervisor disabled (explicit note + enable pointer), store unreachable (full-width error banner; read-only page).
