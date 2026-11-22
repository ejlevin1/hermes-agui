# Agentic "New": spike results vs PLAN assumptions (21 Nov 2026)

Sources:
- S7 = `docs/spikes/S7.md` (dev Mac: Hermes side, mock LLM).
- S8 = `docs/spikes/S8.md` (dev Mac: real CopilotKit 1.90.0 client against a scripted Python endpoint).
- Both are in hermes-items origin/main `bf87b0c`.
- S9 = `S9.md` here (Mac mini: A = S7 re-run on the live Hermes code, plus the multiplexed `/p/<profile>/`; B = 20
  real-model conversations; C = edge).

Verdicts: **VERIFIED** = run and held · **CHANGED** = run, and the plan must change · **UNVERIFIED** = not run (reason given).

Headline:
- The architecture holds.
- The real model filled forms cleanly in 19/20 scripted conversations (95 %, target 90 %), with 0 invalid tool
  calls, 0 create claims and both prompt injections ignored.
- Six things change:
  - slim CopilotKit provider (bundle +164 KB gzip instead of +3.5 MB);
  - Hermes events carry no tool args, so the broker supplies them;
  - an explicit stop path;
  - edge rules (headers within 30 s, heartbeat);
  - a "changes since your last turn" context block plus a chip-only rule for user-owned fields;
  - chat reload needs history replay.
- Six new decisions, D7–D12.

## §3 Architecture

| # | PLAN assumption | Verdict | Evidence | Plan change |
|---|---|---|---|---|
| 3.1 | Browser uses CopilotKit v2 headless hooks + our MUI chat/form | **CHANGED** | S8 §6: `CopilotKitProvider` = +3.5 MB gzip; headless hooks + own ~25-line provider over `CopilotKitCoreReact` = +164 KB gzip, 7/7 tests | Slim provider; pin `@copilotkit/*` exactly (deep `v2/context` imports); provider must track `executingToolCallIds` (HITL otherwise never resolves) and forward headers/credentials (D2) |
| 3.2 | `HttpAgent` POST run (RunAgentInput) ← SSE of AG-UI events | **VERIFIED** | S8 §1–2: transport auto → REST after `GET info`; run = POST `Accept: text/event-stream`; `ag-ui-protocol` `EventEncoder` fits | Endpoint = exactly 4 REST routes (`GET info`, `POST agent/:id/run`, `…/connect`, `…/stop/:threadId`); agent id `default`; `info` minimal, cheap (~9 calls per session), omit `licenseStatus`/`intelligence`/`a2ui` |
| 3.3 | Route under `/api/plugins/items/agui/new`, OIDC user, CSRF, edge allowlist | **CHANGED** (edge) / UNVERIFIED (auth) | S9-C: oauth2-proxy `--upstream-timeout` 30 s → 502 if headers are late; default nginx location cuts at 60 s idle; the `/api/stream` block (buffering off, read_timeout 3600) survives 310 s idle and 11-min holds | New nginx exact locations for GET+POST copied from the `/api/stream` block; send headers immediately + heartbeat ≤ 15 s (D10). OIDC cookie/CSRF through the slim provider's `setHeaders`: UNVERIFIED (needs the real edge + browser → build verify) |
| 3.4 | `hermes-agui` library `AguiBridge(surface=…)` with host hooks | UNVERIFIED | design only; not spiked | Built in Phase 1 with a fake API server + real CopilotKit contract test |
| 3.5 | `build_context` → per-turn system context (types, schemas, tools, form state) | **VERIFIED**, **CHANGED** | S7 Q6 + S9-A: `/v1/runs` `instructions` reach the model each turn, never stored. S9-B: the real model used it correctly in 19/20. S9-B c07/c08: the model trusted its history over the form ("quantity is still 6" when the user set 8) | Add a "Changes since your last turn" block (user edits old → new) to the context (D8) |
| 3.6 | Send only the NEW message (or tool result); Hermes keeps history | **VERIFIED** | S7 Q5 (LLM got stored history + new message), S8 §2 (client re-sends the full history every run: ignore it), S9-B (all multi-turn convs) | none |
| 3.7 | Session id `X-Hermes-Session-Id = f(threadId, user)` | **VERIFIED**, small change | S7 Q2 / S9-A: handler sees the id from body `session_id` on `/v1/runs` (header is for chat completions); multiplexed: key `agent:<profile>:api_server:dm:<sid>`, `HERMES_SESSION_PROFILE` set | Use `/v1/runs` body `session_id`. Prefix ids per surface/profile: a known Hermes issue means bare ids collide across profiles on the multiplexed gateway |
| 3.8 | Translate API-server run events → AG-UI (text, reasoning, backend tool progress, frontend tool calls, finish/error) | **CHANGED** | S7 Q4 + S9-A Q4: frames carry text deltas, `tool.started/completed` with `tool_call_id` only, no args, no results, no reasoning on `/v1/runs`; S8 §3: the client rejects `RUN_FINISHED` while a text message is open | Text → TEXT_MESSAGE_*; backend tools → TOOL_CALL_START/END with empty args (progress only); frontend tool name+args come from the **broker**, correlated by `tool_call_id`; always close TEXT_MESSAGE before RUN_FINISHED/RUN_ERROR. Reasoning: UNVERIFIED with a real model (not captured) |
| 3.9 | Broker: pending frontend calls keyed (session, call id) | **VERIFIED**, **CHANGED** | S7 Q7 + S9-A: same call id in two sessions resolved independently; the spike broker accepted a late result for a dead call (both installs) | The product broker rejects late/duplicate results for dead calls; drops entries on stop |
| 3.10 | Gateway half: native tool `call_frontend_tool` in toolset `agui_frontend`, only on profiles that serve AG-UI | **CHANGED** | S7 Q1 + S9-A: plugin toolsets are ON for every platform once the plugin is enabled (an explicit list doesn't hide them); `tool_search` defers it (items-assist has it `off`, read from live config); multiplexed: a profile without the plugin never gets the tool (S9-A mux) | Gate by enabling the plugin only on the AG-UI profile, plus `check_fn`/`known_plugin_toolsets` for other platforms; doctor asserts the tool is offered. Per-request gating doesn't exist, so step-assist chats on the same profile would also see the tool → D11 dedicated profile |
| 3.11 | The tool reads the current Hermes session id | **VERIFIED** | S7 Q2, S9-A (live + multiplexed): `approval_context` session id + tool call id; `get_session_env("HERMES_SESSION_ID")` | Key = (session id, tool_call_id) from those, not kwargs |
| 3.12 | The tool checks tool_name/args against the schemas the client declared | **CHANGED** | S8 §2: CopilotKit's zod → JSON Schema is lossy (`z.record` → bare object). S9-B: the real model made 0 protocol- or field-invalid calls through the generic wrapper over 20 convs (55+ calls) | The server validates args itself (own schema per tool), not the client's JSON Schema; per-field results go back to the model |
| 3.13 | The tool BLOCKS until the next AG-UI run brings the tool message, then returns in the same Hermes turn | **VERIFIED** in halves; joined UNVERIFIED | Hermes side: S7 + S9-A (blocks of 10/60/300 s complete; run continues). Client side: S8 (run 1 ends at the tool call, the client auto-starts run 2 with the `tool` message, same threadId) | Phase 1 contract test joins them (real CopilotKit client → real bridge → Hermes) |
| 3.14 | Timeouts: HITL waits up to `agui.frontend_tool_timeout_s` (300); on timeout `no_response`, the agent ends its turn; a late result comes as a tagged user message | **VERIFIED**, small change | S7/S9-A Q3: default per-tool cap 420 s (`timed out after 420.0s`); 300 s survives; raising works only via public config `timeouts.tools.sequential_call` or private registry fields; S9 §0: the private field `deadline_floor` is missing in another Hermes build on the same host. Q3d: timeout → LLM continued, run completed. S7 Q5: tool-role injection is impossible, so tagged user text is the only late-result path | Keep 300 (D5). >400 s only via the doctor-checked public config; never mutate registry entries |
| 3.15 | Stop (implied by the UX Stop button) | **CHANGED** | S8 §3: the client calls `POST …/stop/:threadId {runId}` and aborts its fetch. S7/S9-A: `/v1/runs/{id}/stop` cancels the run but does NOT interrupt a blocked handler unless it polls `is_interrupted()` (then exits in ~1.1 s) | Implement the stop route → Hermes stop + broker drop; the handler waits in ≤ 1 s slices checking `is_interrupted()` |
| 3.16 | Generic surface registration; standalone mode later | UNVERIFIED | design only | Phase 1 |
| 3.17 | Packaging: repo `hermes-agui` = plugin + importable module (D3) | UNVERIFIED | not spiked (install/import across plugins not tested) | Phase 1 first task: prove the items plugin can import `hermes_agui` in the dashboard process |
| 3.18 | Edge carries long SSE (PLAN S4) | **CHANGED** | S9-C: nginx → dashboard 660 s hold OK (43 heartbeats); replica legs ≤ 0.08 s added delay; 30 s oauth2-proxy header timeout; Cloudflare docs: 125 s proxy read, 900 s idle, 400 s H2 idle | Headers immediately; heartbeat ≤ 15 s; the gateway↔dashboard broker long-poll stays on 127.0.0.1; browser-through-Cloudflare SSE check is the build's verify step (UNVERIFIED now) |

## §4 UX and frontend tools

| # | PLAN assumption | Verdict | Evidence | Plan change |
|---|---|---|---|---|
| 4.1 | Split view, chat + live form; stacked on narrow screens | UNVERIFIED | UI not built (S8 probe UI only) | — |
| 4.2 | App-side greeting + type chips; the first screen needs no agent | UNVERIFIED | design | — |
| 4.3 | The agent calls `select_item_type`, or offers 2–3 options when unsure | **VERIFIED** | S9-B c04, c05: `ask_choice` with 2 options BEFORE `select_item_type` (2/2); clear cases selected directly (12/12); type switch handled (c14, c15) | — |
| 4.4 | Fills what it can, asks only for what it can't infer, 1–2 questions at a time | **VERIFIED** | S9-B c09–c11: no invented vendor/duration/sensitivity/reason; 1–2 numbered questions; partial fills only for stated values | — |
| 4.5 | User edits anytime, in the form or by chat ("make it 6 months") | **VERIFIED** (agent side) | S9-B c20: "Make it 6 months" → 180 days; "add Snowflake" appended; follow-up turns 4–6 s | UI side UNVERIFIED |
| 4.6 | Review via `open_review`; Create only by the user | **VERIFIED** (agent side) | S9-B: 0 create/submit claims in 20 convs incl. 2 injections asking to "create the item"; `open_review` used after complete fills | UI/audit UNVERIFIED |
| 4.7 | The form is the source of truth and works without the agent | UNVERIFIED | design | — |
| 4.8 | Field ownership: the agent never overwrites a user-edited field; `set_fields` skips it and returns `skipped: user_owned`; the agent asks in chat | **CHANGED** | S9-B: the guard held in every run (no user value overwritten). But c07 (2 of 3 runs): when the user asked IN CHAT to change their own field, the model tried `set_fields` (skipped by the guard) and then confused itself about earlier writes; c08: it restated a stale value | Keep the guard. Skill: a chat request to change a user-owned field → `suggest_values` chip (one click), never `set_fields`. Context: "changes since your last turn" block (D8) |
| 4.9 | Don't fight the cursor (the focused field is locked; agent writes become chips) | UNVERIFIED | UI | — |
| 4.10 | Low confidence → `suggest_values` | **VERIFIED** | S9-B c12 ("top secret" → chips restricted/confidential), c16 (reason chip), c07 stale-env run | — |
| 4.11 | Per-field tool results let the agent self-correct | **VERIFIED** | S9-B c06/c07: the agent read `skipped:user_owned` and explained it; S8 §2: handler result → tool message `content` JSON string | — |
| 4.12 | Visible change log in chat | UNVERIFIED | UI | Same data feeds the context block of 4.8 |
| 4.13 | Never auto-submit; provenance in the item audit | VERIFIED (agent) / UNVERIFIED (audit) | 4.6 | — |
| 4.14 | Accessibility (aria-live, no focus steal) | UNVERIFIED | UI | — |
| 4.15 | Latency: streamed text, "filling…" shimmer, Stop | **CHANGED** | S9-B: every frontend tool is its own LLM round trip (2–7 s each uncontended). Turn wall time solo median 13.7 s (p90 21.6), 4-parallel under host load 17–21 median 42.9 s (p90 56.5); follow-ups 4–6 s. Stop: S8 + S7/S9-A | Budget for 10–20 s first turns; the filling state is mandatory; cut round trips with a combined apply tool (D9) |
| 4.16 | Drafts: form + thread id in localStorage; reload resumes the same Hermes session | **CHANGED** | S8 §4: the CopilotKit client keeps nothing across reload; headless hooks never call `connect`; `connect` renders whatever `MESSAGES_SNAPSHOT` the server replays (empty run = empty chat, no error) | The page calls `connectAgent` itself; the endpoint replays the Hermes session as `MESSAGES_SNAPSHOT` or returns empty (D7) |
| 4.17 | Type switch after fields are filled: confirm, carry over same-key fields | VERIFIED (agent) / UNVERIFIED (confirm UI) | S9-B c14/c15: the agent switched and told the user the old values were dropped | — |
| 4.18 | Frontend tools v1 (`select_item_type`, `set_fields`, `suggest_values`, `focus_field`, `ask_choice`, `open_review`) through one generic `call_frontend_tool` | **VERIFIED** | S9-B: all used correctly except `focus_field` (never chosen); HITL `ask_choice`: client side S8, Hermes side mock 300 s (S7/S9-A); real-model long HITL wait UNVERIFIED (simulator answers instantly) | Consider an `apply_form(type_key?, values, open_review?)` tool (D9) |
| 4.19 | Agent context each run via `useAgentContext` | **CHANGED** (shape) | S8 §2: arrives as `context[]` `{description, value: <JSON string>}`, refreshed each run; S7 Q6: goes to the model only via `instructions` | Bridge folds `context[]` + server-side truth (types the user may create, ownership) into `instructions`; never trust client context for authorization |
| 4.20 | Backend tools: items MCP read-only (`type_list`, `inventory_search`) | UNVERIFIED | S9-B had no MCP tools (live items data off-limits) | Phase 2 verify |
| 4.21 | Agent = profile `items-assist` + new skill `items-new-agentic` | **CHANGED** | S9-A/S7: the plugin tool can't be hidden per request on one profile; live items-assist SOUL is step-assist ("propose changes with `item_update_fields`"); S9-B ran with its own persona + rules | Dedicated profile `items-new` (D11); skill text from S9-B rules + B-1 changes |
| 4.22 | CSP / license / bundle | **VERIFIED** with changes | S8 §5–6: no license warning or banner, no foreign requests; one zod `eval` → `config({jitless:true})` as the first import | Entry starts with `import "./zodConfig"`; `enableInspector={false}`; npm `SCARF_ANALYTICS=false` |

## Not covered by any spike (carry into the builds)
- Real CopilotKit client → real bridge → real Hermes, end to end (Phase 1 contract test).
- The authenticated browser through Cloudflare + oauth2-proxy on the new routes (Phase 2 verify, real browser).
- The real model on a long human wait.
- The production skill text with real item types + MCP read tools: re-run `s9-evidence/scripts/b` before release.
- Gateway↔dashboard broker auth.
- Reasoning events with a real model.
