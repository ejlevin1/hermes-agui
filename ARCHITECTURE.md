# Architecture

hermes-agui connects a browser UI built with [CopilotKit](https://github.com/CopilotKit/CopilotKit) (or any
[AG-UI](https://github.com/ag-ui-protocol/ag-ui) client) to a [Hermes Agent](https://github.com/NousResearch/hermes-agent)
profile. It also lets the agent call **frontend tools**: actions that run in the user's browser, such as filling a form
field, with the result going back to the agent.

It has two halves:

| Half | Runs in | Code | Job |
|---|---|---|---|
| **Bridge** (library) | The host app's process, e.g. a Hermes dashboard plugin | `hermes_agui/` | CopilotKit runtime-v2 + AG-UI endpoints, Hermes API client, event translation, transcript, broker |
| **Gateway plugin** | The Hermes gateway, on profiles that serve agentic surfaces | `plugin/` | The `call_frontend_tool` tool the model calls |

Placeholders used below: `<host>` is the host app's mount prefix, `<profile>` is the Hermes profile that serves the
surface.

## Concepts

- **Surface:** one agent-assisted UI, such as a form, registered by the host app. Its `id` is the CopilotKit
  `agentId`. A surface defines the Hermes profile that serves it, who may use it (`authorize`), the per-turn
  instructions (`build_instructions`), and the frontend tools it allows (`FrontendToolSpec`, whose JSON Schemas live on
  the server and are authoritative).
- **Thread → Hermes session:** each CopilotKit thread maps to one Hermes session, `<surface>:<principal>:<threadId>`.
- **Turn vs run:** one Hermes turn can span several AG-UI runs. Run 1 ends when the agent calls a frontend tool. The
  browser executes the tool and starts run 2 carrying the result, and the bridge keeps streaming the same Hermes turn
  into it.
- **Broker:** an in-memory meeting point. The gateway registers each frontend call there with its name and args, and
  the bridge resolves it with the browser's result. It is reachable only on loopback, with a bearer token.

## C4: system context

```mermaid
C4Context
  title System context: agent-assisted UI with hermes-agui

  Person(user, "User", "Fills a form together with an agent")
  System(host, "Host app", "Web app with agent-assisted surfaces, e.g. a Hermes dashboard plugin. Embeds the hermes-agui bridge")
  System_Ext(hermes, "Hermes Agent gateway", "Runs the profile that serves the surface; API server /v1/runs")
  System_Ext(llm, "LLM provider", "The model behind the profile")
  System_Ext(edge, "Edge (reverse proxy / auth)", "TLS, login, exact-path allowlist")

  Rel(user, edge, "Uses", "HTTPS")
  Rel(edge, host, "Forwards allowed paths", "HTTP + SSE")
  Rel(host, hermes, "Starts runs, streams events, stops runs", "HTTP + SSE, API key")
  Rel(hermes, host, "Registers frontend tool calls, polls results", "HTTP loopback, broker token")
  Rel(hermes, llm, "Completions with tools")
```

## C4: containers

```mermaid
C4Container
  title Containers

  Person(user, "User")

  System_Boundary(browser, "Browser") {
    Container(spa, "Host SPA", "React + CopilotKit v2", "Chat + form; registers frontend tools (useFrontendTool) and form state (useAgentContext); runtimeUrl = the bridge")
  }

  System_Boundary(hostp, "Host app process") {
    Container(hostcode, "Host plugin code", "Python", "Surfaces: authorize, build_instructions, tool schemas; the human commit actions (Create / Save)")
    Container(bridge, "hermes-agui bridge", "Python, FastAPI router", "CopilotKit runtime v2 + AG-UI routes, Translator, MemoryBroker, TranscriptStore")
    ContainerDb(store, "Transcript store", "SQLite", "Messages per thread, for replay on reload")
  }

  System_Boundary(gw, "Hermes gateway") {
    Container(api, "API server", "Hermes", "/v1/runs, /v1/runs/{id}/events, /v1/runs/{id}/stop")
    Container(agent, "Agent loop (profile)", "Hermes", "Model + toolsets; per-turn instructions")
    Container(plugin, "agui_frontend plugin", "Hermes plugin, stdlib", "Tool call_frontend_tool")
  }

  System_Ext(llm, "LLM provider")

  Rel(user, spa, "Types, edits fields, clicks")
  Rel(spa, bridge, "GET info; POST agent/{id}/run|connect|stop", "HTTPS, SSE (AG-UI events)")
  Rel(bridge, hostcode, "authorize / build_instructions")
  Rel(bridge, store, "append / read")
  Rel(bridge, api, "start_run, events, stop", "HTTP + SSE")
  Rel(api, agent, "Runs turn")
  Rel(agent, llm, "Completions")
  Rel(agent, plugin, "Calls call_frontend_tool(tool_name, args)")
  Rel(plugin, bridge, "POST broker/call; GET broker/result", "loopback HTTP, bearer token")
  Rel(spa, hostcode, "Commit (Create / Save): only on a human click", "HTTPS")
```

## C4: components of the bridge

```mermaid
C4Component
  title Components: hermes_agui (inside the host process)

  Container_Boundary(lib, "hermes_agui") {
    Component(router, "make_router", "runtime.py", "Routes info, agent/{id}/run|connect|stop, agui/{id}. Headers + RUN_STARTED at once; SSE heartbeat every 10 s; one _Active entry per Hermes session")
    Component(reg, "SurfaceRegistry / Surface", "surface.py", "Host-registered surfaces; session id prefixing; Principal, TurnContext, FrontendToolSpec")
    Component(tr, "Translator", "translate.py", "Hermes run events to AG-UI events; frontend calls become TOOL_CALL_START/ARGS/END with the real name + args")
    Component(hc, "HttpHermesClient", "hermes_client.py", "/p/<profile>/v1/runs, events SSE, stop")
    Component(br, "MemoryBroker + broker_router", "broker.py", "Calls keyed by (session, tool_call_id); schema validation (422); late/duplicate results rejected; stop drops the session (410)")
    Component(st, "SqliteTranscriptStore", "store.py", "Thread transcript for connect replay")
  }

  Container_Ext(hermes, "Hermes API server")
  Container_Ext(gwplugin, "agui_frontend plugin")
  Container_Ext(spa, "Browser (CopilotKit)")

  Rel(spa, router, "run / connect / stop / info")
  Rel(router, reg, "resolve surface, authorize, instructions")
  Rel(router, hc, "start_run / events / stop")
  Rel(hc, hermes, "HTTP + SSE")
  Rel(router, tr, "frames in, AG-UI events out")
  Rel(tr, br, "wait_call(session, tool_call_id) for name + args")
  Rel(router, br, "open_session / resolve / drop_session")
  Rel(router, st, "append / messages")
  Rel(gwplugin, br, "register call / poll result")
```

## Flows

### 1. A plain chat turn

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (CopilotKit)
  participant R as Bridge router
  participant S as Surface (host code)
  participant H as Hermes API server
  participant M as Model

  B->>R: POST agent/{surface}/run (RunAgentInput: threadId, runId, messages, context[])
  R-->>B: 200 + RUN_STARTED (before anything slow)
  R->>S: authorize(request) then build_instructions(TurnContext)
  R->>H: POST /p/<profile>/v1/runs {session_id, input = trailing user message only, instructions}
  H->>M: completion
  loop events
    H-->>R: message.delta
    R-->>B: TEXT_MESSAGE_START / CONTENT
  end
  Note over R,B: ": heartbeat" SSE comment every 10 s while quiet
  H-->>R: run.completed
  R-->>B: TEXT_MESSAGE_END, RUN_FINISHED
  R->>R: store.append(user + assistant messages)
```

The client re-sends the full history on every run. The bridge sends Hermes only the new trailing message, because
Hermes keeps the session itself. The per-turn `instructions` carry live state (form values, ownership, allowed tools)
and are never stored by Hermes.

### 2. A frontend tool call (one Hermes turn, two AG-UI runs)

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (CopilotKit)
  participant R as Bridge router + Translator
  participant K as Broker
  participant H as Hermes API server
  participant P as call_frontend_tool (gateway plugin)

  B->>R: POST run (run 1)
  R-->>B: RUN_STARTED
  R->>K: open_session(session, surface tools)
  R->>H: POST /v1/runs
  H->>P: model calls call_frontend_tool(tool_name="set_fields", args)
  P->>K: POST broker/call {session_id, tool_call_id, tool_name, args} (bearer token)
  K->>K: validate args against OUR schema (422 + errors back to the model if invalid)
  H-->>R: tool.started {tool_call_id} (Hermes events carry no name/args)
  R->>K: wait_call(session, tool_call_id)
  K-->>R: {name: set_fields, args}
  R-->>B: TOOL_CALL_START / ARGS / END (real name + args)
  R-->>B: RUN_FINISHED (run 1 ends, the Hermes turn keeps waiting)
  loop every <= 1 s until result, stop or timeout
    P->>K: GET broker/result?session_id&tool_call_id
    K-->>P: 204 pending
  end
  B->>B: useFrontendTool handler applies the fields, builds per-field results
  B->>R: POST run (run 2) with messages ending in {role: tool, toolCallId, content}
  R-->>B: RUN_STARTED
  R->>K: resolve(session, tool_call_id, content)
  P->>K: GET broker/result
  K-->>P: 200 result
  P-->>H: tool result returned to the model
  H-->>R: message.delta ... run.completed (same event stream as run 1)
  R-->>B: TEXT_MESSAGE_*, RUN_FINISHED
```

### 3. Human-in-the-loop tool (the agent asks, the user clicks)

```mermaid
sequenceDiagram
  autonumber
  participant U as User
  participant B as Browser
  participant R as Bridge
  participant K as Broker
  participant P as call_frontend_tool

  P->>K: broker/call ask_choice(question, options)
  R-->>B: TOOL_CALL_* ask_choice, RUN_FINISHED
  B->>U: renders the choice chips
  Note over P,K: plugin keeps polling in 1 s slices (default limit 300 s, below Hermes' 420 s per-tool cap)
  U->>B: clicks an option
  B->>R: POST run with the tool message {choice}
  R->>K: resolve
  K-->>P: 200 {choice}
  Note over B,R: if the user types a chat message instead, the pending call resolves as {status: user_replied} and the message is queued as the next turn
```

### 4. Stop

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant R as Bridge
  participant H as Hermes API server
  participant K as Broker
  participant P as call_frontend_tool

  B->>R: POST agent/{surface}/stop/{threadId}
  R->>H: POST /v1/runs/{id}/stop
  R->>K: drop_session(session)
  K-->>P: next poll returns 410 dropped
  P->>P: tools.interrupt.is_interrupted() is true, so it exits within ~1 s
  R-->>B: {stopped: true}, and an open stream closes its text and ends the run
  Note over K: a late or duplicate tool result for this call is rejected
```

### 5. Reload: the chat comes back

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant R as Bridge
  participant T as Transcript store

  B->>B: page load restores threadId and form draft from localStorage (host UI)
  B->>R: POST agent/{surface}/connect {threadId}
  R->>T: messages(thread key)
  R-->>B: RUN_STARTED, MESSAGES_SNAPSHOT [...], RUN_FINISHED
  Note over B,R: no transcript means an empty run, and the chat starts fresh
```

### 6. The browser never answers

```mermaid
sequenceDiagram
  autonumber
  participant P as call_frontend_tool
  participant K as Broker
  participant M as Model

  loop until timeout_s (default 300 s)
    P->>K: GET broker/result
    K-->>P: 204
  end
  P-->>M: {status: "no_response"}
  Note over M: the model continues (e.g. asks in chat), and a result arriving later is rejected by the broker
```

## Security model

- **Authorization is the host's job.** Every route calls `Surface.authorize(request)` with the host's own session and
  CSRF checks. Client `context[]` is data for the prompt and never grants access.
- **Frontend tool args are validated server-side** against the surface's `FrontendToolSpec` schemas. CopilotKit's
  zod-to-JSON-schema conversion is lossy, so the server schemas win. An invalid call returns the errors to the model
  instead of reaching the browser.
- **The broker is loopback-only** and protected by a bearer token: `AGUI_BROKER_TOKEN` in the profile `.env`, compared
  in constant time. Hosts must never route `broker/*` through the public edge.
- **The agent never commits.** Creating or saving is a human click in the host UI. Frontend tools only change local
  form state.
- **Session ids are prefixed per surface**, so threads can't collide across surfaces or profiles on a multiplexed
  gateway.

## Edge requirements

- Send headers and `RUN_STARTED` immediately. Some auth proxies return 502 when headers take more than about 30 s.
- Keep the SSE heartbeat (every 10 s) under the shortest idle timeout on the path: nginx defaults to 60 s, and CDNs
  commonly allow about 100–125 s.
- Serve the run/connect paths with proxy buffering off and a long read timeout. Allow-list exact paths. Stop needs a
  pattern match because of `{threadId}`.

## Deployment

```mermaid
flowchart LR
  subgraph Browser
    SPA["Host SPA + CopilotKit v2"]
  end
  subgraph Edge
    PX["Reverse proxy + auth\n(exact-path allowlist, SSE: buffering off)"]
  end
  subgraph HostProc["Host process (e.g. Hermes dashboard)"]
    HP["Host plugin"] --> LIB["hermes_agui (pinned git dependency)"]
    LIB --> DB[("transcripts.sqlite")]
  end
  subgraph Gateway["Hermes gateway"]
    API["API server /p/&lt;profile&gt;/v1/runs"]
    PL["agui_frontend plugin\n(enabled only on &lt;profile&gt;)"]
  end
  SPA -- "HTTPS + SSE" --> PX --> HP
  LIB -- "HTTP + SSE, API key" --> API
  PL -- "loopback HTTP, AGUI_BROKER_TOKEN" --> LIB
```

Install steps and the tested packaging options are in [`docs/packaging.md`](docs/packaging.md). The design history and
spike evidence are in [`docs/design/`](docs/design/).
