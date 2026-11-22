# Agentic forms for hermes-items (first surface: "New (agentic)") + generic Hermes AG-UI bridge: PLAN v3 (21 Nov 2026)

Status: spikes S7/S8/S9 done, nothing built. v1 = `PLAN-v1.md`, v2 (after spikes) = `PLAN-v2.md`.
v3 applies Eric's answers (21 Nov 2026): D2 = whatever is easiest in code → full `CopilotKitProvider`; D7 = the chat
reappears; D8 = a chat request overwrites the user's field locally, saving only with the user's permission; D11 = a
GENERIC agentic-form structure any form flow can use (§3a); D9/D10/D12 = recommended. Builds open after Eric sees §5.

## 0. Goal (unchanged)
A left-nav entry **New (agentic)** next to the classic **New**. You describe what you need; an agent picks the item
type and fills the intake form with you, live, while you can always edit the form yourself. Creation stays a human
click. Built on an open protocol (AG-UI), so the bridge is reusable as its own Hermes extension.

## 1. What was checked first (unchanged from v1, still true)
- No AG-UI in Hermes core.
- Upstream PR #65845 is open and `needs-decision`.
- CopilotKit's direct AG-UI mode (`selfManagedAgents`) is a licensed tier, so we use the free `runtimeUrl` path.
- The catalog only has patterns.
- A2UI is deferred.
- Details: `PLAN-v1.md` §1–2.

## 2. What the spikes proved (short)
- **Hermes side (S7 dev Mac, S9-A live code incl. multiplexed `/p/<profile>/`):**
  - A plugin tool can block for 300 s inside an API-server turn and sees the Hermes session id + tool call id.
  - Per-turn context via `/v1/runs` `instructions` reaches the model and is never stored.
  - Only the new message needs sending.
  - Two sessions with the same call id don't collide.
  - Tool-role messages can't be injected.
  - Events carry no tool args/results.
  - The default per-tool cap is 420 s.
  - Stop doesn't interrupt a blocked handler unless it polls.
- **Client side (S8, real CopilotKit 1.90.0):**
  - The v2 client works against a hand-written Python endpoint (4 REST routes).
  - A frontend tool ends run 1 and the client starts run 2 with the result itself.
  - HITL waits for the click.
  - No license nags for the hooks we use.
  - `CopilotKitProvider` is too big (+3.5 MB gzip); the headless hooks + an own provider add +164 KB.
  - One zod `eval` needs `jitless`.
  - The client keeps nothing across reload.
- **Real model (S9-B, items-assist's sonnet via the Claude pool, 20 scripted conversations):**
  - **19/20 clean (95 %)**, 0 invalid tool calls, 0 create claims, both prompt injections ignored and reported.
  - It asks before choosing between two types and asks instead of guessing missing values.
  - Failure mode: when the user asks in chat to change a field they edited, the model tries to write it (the guard
    blocks it) and can restate stale values.
  - Turns take 7–22 s solo, longer under load.
- **Edge (S9-C):**
  - oauth2-proxy returns 502 for any response whose headers take > 30 s.
  - nginx's default location cuts at 60 s idle.
  - The `/api/stream`-style block carries 11-min SSE holds and 310 s idle gaps.
  - Cloudflare: 125 s read / 900 s idle (docs).

## 3. Architecture (corrected)
```
Browser (items React app)
  CopilotKit v2 full CopilotKitProvider + hooks (D2)  + our MUI chat/form, via the generic useAgenticForm (§3a)
  ── GET info · POST agent/<surface>/run (RunAgentInput) ← SSE AG-UI · POST …/connect · POST …/stop/:threadId ──┐
                                                                                                                 ▼
Edge: Cloudflare → nginx exact locations (GET+POST, buffering off, read_timeout 3600, like /api/stream) → oauth2-proxy
Dashboard process: items plugin routes /api/plugins/items/agui/{info,agent/:id/run,agent/:id/connect,agent/:id/stop/:tid}
  └ hermes-agui library: AguiBridge(surfaces=[items.new, items.step, …])  (agentId = surface, §3a)
      - authorize(request) → principal (items authz)                                   [host hook]
      - build_context(principal, input) → `instructions` for this turn: types the user may create + field schemas,
        frontend tool list + OUR schemas, form state + ownership from input.context[], and a
        "changes since your last turn" block (D8)                                       [host hook]
      - POST /v1/runs on profile items-agentic (D11) with body session_id = "<surface>:<user>:<threadId>", input = the
        trailing new message only (the client re-sends the full history every run: ignore it)
      - response headers + RUN_STARTED at once; SSE comment heartbeat every ≤ 15 s (edge: 30 s / 60 s / 125 s limits)
      - Hermes events → AG-UI: message.delta → TEXT_MESSAGE_*; backend tool.started/completed → TOOL_CALL_START/END
        with empty args (progress only); call_frontend_tool → TOOL_CALL_START/ARGS/END with the REAL tool name and
        args taken from the BROKER (Hermes events carry neither; correlate by tool_call_id); run.completed →
        TEXT_MESSAGE_END + RUN_FINISHED; failed/cancelled → close open text, RUN_ERROR/RUN_FINISHED
      - broker: pending frontend calls keyed (Hermes session id, tool_call_id); rejects late/duplicate results for
        dead calls; drops entries on stop
      - stop/:threadId {runId} → POST /v1/runs/{id}/stop + broker drop
      - connect → replay Hermes session history as MESSAGES_SNAPSHOT (D7) or an empty run
                         ▲ long-poll on 127.0.0.1 only (token-authenticated; never through the edge)
Gateway process (profile items-agentic)                                                     │ /v1/runs + events SSE
  hermes-agui gateway half: plugin tool call_frontend_tool(tool_name, args), enabled ONLY on profile items-agentic
  (plugin toolsets are on for every platform of a profile that enables the plugin; per-request hiding doesn't exist);
  check_fn / known_plugin_toolsets keep it off other platforms; tools.tool_search off (else it is deferred).
  Handler: session id + tool_call_id from approval_context; validates tool_name/args against OUR schema (CopilotKit's
  zod→JSON schema is lossy); registers with the broker; waits in ≤ 1 s slices checking tools.interrupt.is_interrupted();
  returns the browser's per-field result, or {"status":"no_response"} at agui.frontend_tool_timeout_s (300, D5).
  Items data stays on the items MCP facade (read-only tools for this surface).
```
- One Hermes turn spans several AG-UI runs: run 1 ends at TOOL_CALL_END, and the client starts run 2 carrying the
  `tool` message. The bridge resolves the broker with it and streams the rest of the same Hermes turn into run 2.
  Both halves are verified separately; the Phase 1 contract test joins them.
- Timeouts:
  - The Hermes per-tool cap is 420 s (default), so 300 s fits.
  - Longer waits only through the doctor-checked public config `timeouts.tools.sequential_call`. Never write private
    registry fields: they differ between Hermes builds even on one host (S9 §0).
  - A late result after `no_response` arrives as a tagged user message. Tool-role injection is impossible, and Hermes
    prefixes a run on a session with pending tool output with an "ignore pending results" note.
- Session ids are prefixed per surface, because bare API-server ids collide across profiles on the multiplexed gateway.
- Generic vs host-specific (unchanged): hermes-agui knows nothing about items. Hosts register a surface (id, profile,
  authorize, build_context, allowed frontend tools + schemas, limits).
- Packaging (D3, unchanged, still untested): repo `ejlevin1/hermes-agui` = plugin (gateway tool + broker routes) +
  module `hermes_agui`. The first Phase 1 task proves the items plugin can import it in the dashboard process. Items
  doctor checks that the plugin is installed on `items-agentic`, the tool is offered, tool_search is off and the timeout
  config is consistent.

## 3a. Generic agentic forms (D11)
Goal: any schema-driven form flow in the app becomes agentic with a few lines, sharing one bridge, one profile, one
tool set and one UI pattern. "New (agentic)" is the first surface; the current step's fields on an item are the second.

Grounding: every editable form in items already renders through one component, `FieldForm({fields, values,
onChange, errors})` (`web/src/components/fields.tsx`), used by New item (intake) and the item step panel; values are
`Record<key, EditValue>` and saving is always a human click (`item/create`, `item/fields`, `item/act`).

**A surface** = (id, purpose, the form's fields + values, a human commit action, optional extra frontend tools).
Registered server-side as data (Eric: policy as editable data):
| field | example `items.new` | example `items.step` |
|---|---|---|
| id = CopilotKit agentId | `items.new` | `items.step` |
| thread key | user + draft id | user + item id + step run |
| context builder | types the user may create + intake schemas | item, step, step fields, readiness, comments |
| extra frontend tools | `select_item_type` | none (actions stay human buttons) |
| commit (human only) | Create → `item/create` | Save → `item/fields` (proposal rules unchanged) |
| instructions addendum | short text, editable in Admin later | idem |
| MCP read tools | `type_list`, `inventory_search` | `item_get`, `inventory_search` |

**Shared pieces**
- *Bridge (hermes-agui)*: one CopilotKit-runtime endpoint per host app; `agent/:agentId/*` = the surface. `info`
  lists the surfaces the user may use. Nginx exact locations are rendered from the surface list (D10).
- *Profile*: ONE profile `items-agentic` for all agentic surfaces (the plugin tool can't be hidden per request, so it
  must not live on items-assist; one profile, not one per surface). Generic skill `agentic-form` = the S9-B rules
  (ownership, ask-don't-guess, review, never commit, injection handling) + the surface addendum per turn.
- *Frontend tools (same for every surface)*: `apply_form(values, open_review?)` (D9), `set_fields`, `suggest_values`,
  `ask_choice`, `focus_field`, `open_review`, plus the surface's extras. Per-field results as before.
- *Web*: `web/src/agentic/` (no items imports, extractable later): `useAgenticForm({surface, fields, values,
  setValues, commit})` registers the tools + context with CopilotKit, keeps the ownership map, undo stack, change log
  and "changes since your last turn" data; `<AgenticLayout>` = split view (chat + form, stacked on narrow screens);
  `FieldForm` gains optional `ownership`/`highlight` props. Opting a page in = wrap its existing `FieldForm`.
- *Ownership (D8)*: fields are `empty | agent | user`. The agent writes empty/agent fields freely. A user-owned field
  changes only when the user asked for it in chat: `set_fields(..., user_requested=true)` → applied LOCALLY in the
  form, highlighted, one-click undo, listed in the review panel as "changed at your request". Nothing reaches the
  server until the user clicks the surface's commit button (Create / Save). On an existing item this means the agent
  never saves: its edits are a local draft until you press Save.
- *Chat continuity (D7)*: `connect` replays the Hermes session as `MESSAGES_SNAPSHOT`; the page calls connect on
  load; the form draft + thread id live in localStorage per thread key.
- Later: extract `web/src/agentic/` into a package inside the hermes-agui repo once a second app wants it; the
  Python side is generic from day one.

## 4. UX: the "New (agentic)" page (corrected)
Layout and flow as v1 (split view; greeting + type chips; describe → type → fill → review → the user presses
Create). Changes:
- **Latency budget:**
  - First turns take ~10–20 s (each frontend tool is one LLM round trip of 2–7 s); follow-ups take 4–6 s.
  - The "filling…" state on target fields and streamed text are mandatory, not polish.
  - Stop is always visible.
  - A combined `apply_form` tool cuts round trips (D9).
- **User-owned fields (D8 = Eric):**
  - Without a chat request the guard stays: `set_fields` skips them (`skipped:user_owned`); it held in every run.
  - When the user asks in chat to change a field they edited, the agent calls `set_fields(..., user_requested=true)`:
    the form changes locally (highlight + undo), and it is saved only when the user commits.
- **Change awareness:**
  - Every turn's context lists the user's edits since the agent's last turn (old → new), so the agent doesn't restate
    stale values.
  - The visible chat change log uses the same data.
- **Reload:**
  - The page calls `connectAgent` on load.
  - The endpoint replays the Hermes session (D7).
  - Form values + thread id stay in localStorage as v1.
- **Frontend tools v1:**
  - `select_item_type`, `set_fields`, `suggest_values`, `focus_field`, `ask_choice`, `open_review`, plus
    `apply_form` if D9 = yes.
  - Each returns per-field results (applied | skipped:user_owned | invalid:<msg> | unknown_field).
  - The real model used them correctly through the generic wrapper; `focus_field` was never chosen.
- **Context:**
  - CopilotKit sends `context[]` as `{description, value: <JSON string>}`.
  - The bridge folds it into `instructions` together with server-side truth (which types this user may create, the
    ownership map). Client context is never used for authorization.
- **Client build:**
  - Full `CopilotKitProvider` (D2: least code; S8 measured +3.5 MB gzip on top of the 195 KB app, cached after the
    first load). Headers for CSRF + credentials for the OIDC cookie via its `headers`/`credentials` props.
  - `import "./zodConfig"` (`jitless`) is the first import; `enableInspector={false}`; exact pins; SCARF off.
- **Agent:**
  - Profile `items-agentic` (D11, shared by every agentic surface) with its own SOUL.
  - Skill `agentic-form` = the rule block S9-B used (`s9-evidence/scripts/b/b_run.py` `RULES`), plus the
    `user_requested` rule for user-owned fields and the "changes since last turn" convention; surface addendum per turn.

## 5. Delivery (v3)
- Phase 0 spikes: DONE (S7, S8, S9). Results: `SPIKE-RESULTS.md`.
- **Build A: `hermes-agui` 0.1.0** (new repo `ejlevin1/hermes-agui`; devx `project` tag needed — reuse `items` unless
  Eric adds `agui`).
  - Lane 1 (first, gating): cross-plugin import proof — the items dashboard plugin imports `hermes_agui` in the live
    dashboard process layout (D3); if it fails, fall back to vendoring the module into items (decided in-build).
  - Bridge (CopilotKit runtime v2 REST: info, agent/:id/run|connect|stop; plain AG-UI route), multi-surface registry,
    connect replay (D7), headers-first + ≤ 15 s heartbeat; broker (127.0.0.1, token, late-result rejection, stop drop);
    gateway tool `call_frontend_tool` (interrupt polling, own schema validation, 300 s); doctor helpers.
  - Contract test: real pinned `@copilotkit/react-core` (full provider) → real bridge → Hermes gateway on a mock LLM:
    frontend tool across two runs, HITL, stop, reload/connect replay, two surfaces.
  - Release: tag + install docs. Not deployed alone; Build B deploys it.
- **Build B: hermes-items 0.3.0** (after A's release):
  - Generic layer: surface registry (data), `items-agentic` profile + `agentic-form` skill, context builders,
    `web/src/agentic/` (`useAgenticForm`, `<AgenticLayout>`, ownership/undo/change log, FieldForm props).
  - Surface 1: left-nav **New (agentic)** (`items.new`, `select_item_type`, review → Create, provenance in audit).
  - Surface 2: the item step panel gets an "Ask the agent to fill this step" toggle (`items.step`, Save stays human) —
    proves the structure is generic. (If Eric prefers, move to 0.3.1.)
  - nginx exact locations rendered from the surface list (D10); itemsctl install/doctor for the profile + plugin.
  - Deploy; verify with a real browser through Cloudflare (long SSE, heartbeat, stop, reload replay, both surfaces);
    reliability gate D12 (S9-B harness with the real skill + types, ≥ 90 % clean blocks release otherwise).
- Later: designer interview as a surface; A2UI `render_surface`; retire the old assist panel once `items.step` covers it.

## 6. Decisions (final, 21 Nov 2026)
- D1 CopilotKit runtime v2 endpoints in our plugin + plain AG-UI route. VERIFIED.
- D2 Full `CopilotKitProvider` (Eric: easiest code). Cost: +3.5 MB gzip bundle.
- D3 Separate repo `hermes-agui`; import proof first.
- D4 Split view.  D5 HITL wait 300 s.  D6 Classic New stays.
- D7 Chat reappears on reload (connect replays the Hermes session).
- D8 A chat request overwrites the user's field locally (highlight + undo); saved only when the user commits.
- D9 `apply_form` combined tool + granular tools.
- D10 Additive nginx exact locations + headers-first/heartbeat; no oauth2-proxy change.
- D11 Generic agentic-form surfaces (§3a), one shared profile `items-agentic`.
- D12 Reliability gate per release (≥ 90 % on the S9-B harness).
