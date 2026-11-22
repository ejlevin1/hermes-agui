# Architecture

hermes-agui connects a browser UI built with [CopilotKit](https://github.com/CopilotKit/CopilotKit) (or any
[AG-UI](https://github.com/ag-ui-protocol/ag-ui) client) to a [Hermes Agent](https://github.com/NousResearch/hermes-agent)
profile. It also lets the agent call **frontend tools**: actions that run in the user's browser, such as filling a form
field, with the result going back to the agent.

**Nothing here is a separate service.** hermes-agui ships two pieces, and both run inside processes that already
exist:

| Piece | What it is | Runs inside | Code |
|---|---|---|---|
| **hermes_agui** | Python **library**, imported by a host app's Hermes **dashboard plugin** | The Hermes dashboard process | `hermes_agui/` |
| **agui_frontend** | Hermes **gateway plugin** that registers the tool `call_frontend_tool` | The Hermes gateway process, enabled only on the profile that serves the UI | `plugin/` |

The diagrams use the same names everywhere. The C4 diagrams below are drawn as Mermaid flowcharts with C4 notation,
because Mermaid's native C4 renderer overlaps text on GitHub.

**Legend:**
- Dark blue = person.
- Blue = container (something that runs or stores data).
- Light blue = component inside a container.
- Grey = external system.
- Dashed boxes = OS processes.
- Each box says **what kind of thing it is** in brackets.

## Names used in every diagram

| Name | Kind | Lives in |
|---|---|---|
| **Host SPA** | Web app (React + CopilotKit v2) | Browser |
| **Edge proxy** | Reverse proxy + login (e.g. nginx + an auth proxy) | Its own container(s) |
| **Host plugin** | Hermes dashboard plugin of the host app (e.g. hermes-items) | Hermes dashboard process |
| **Surface** | Host code: one agent-assisted UI (authorize, instructions, tool schemas) | Host plugin |
| **agui router** | `make_router` (CopilotKit runtime v2 + AG-UI routes) | hermes_agui, in the Host plugin |
| **Translator** | Hermes events → AG-UI events | hermes_agui |
| **Broker** | `MemoryBroker` + its HTTP routes `broker_router` | hermes_agui |
| **Hermes client** | `HttpHermesClient` | hermes_agui |
| **Transcript store** | `SqliteTranscriptStore` (a SQLite file) | hermes_agui |
| **Hermes API server** | `/p/<profile>/v1/runs` | Hermes gateway process |
| **Agent loop** | The profile's model + tools loop | Hermes gateway process |
| **call_frontend_tool** | The tool of the agui_frontend gateway plugin | Hermes gateway process |
| **LLM** | Model provider | External |

## C4 level 1: system context

```mermaid
flowchart TB
  user(["<b>User</b><br/>[Person]<br/>fills a form together with an agent"])
  host["<b>Host app</b><br/>[Software system: Hermes dashboard plugin]<br/>agent-assisted pages, embeds hermes_agui"]
  edge["<b>Edge proxy</b><br/>[External system]<br/>TLS, login, exact-path allowlist"]
  gw["<b>Hermes gateway</b><br/>[Software system]<br/>runs the profile that serves the UI"]
  llm["<b>LLM</b><br/>[External system]<br/>model provider"]

  user -->|"uses<br/>HTTPS"| edge
  edge -->|"forwards allowed paths<br/>HTTP + SSE"| host
  host -->|"starts / streams / stops runs<br/>HTTP + SSE, API key"| gw
  gw -->|"frontend tool calls<br/>loopback HTTP, token"| host
  gw -->|"completions"| llm

  classDef person fill:#08427b,stroke:#052e56,color:#ffffff
  classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
  classDef ext fill:#6b6b6b,stroke:#4d4d4d,color:#ffffff
  class user person
  class host,gw system
  class edge,llm ext
```

## C4 level 2: containers and where they run

```mermaid
flowchart LR
  user(["<b>User</b><br/>[Person]"])

  subgraph P1["Process: web browser"]
    spa["<b>Host SPA</b><br/>[Container: React + CopilotKit v2]<br/>chat + form, frontend tool handlers"]
  end

  edge["<b>Edge proxy</b><br/>[Container: nginx + auth proxy]<br/>exact paths, SSE buffering off"]

  subgraph P2["Process: Hermes dashboard (Python)"]
    hp["<b>Host plugin</b><br/>[Container: Hermes dashboard plugin]<br/>Surfaces + hermes_agui library<br/>(imported, same process)"]
    ts[("<b>Transcript store</b><br/>[Container: SQLite file]<br/>chat replay")]
  end

  subgraph P3["Process: Hermes gateway (Python)"]
    api["<b>Hermes API server</b><br/>[Container: Hermes core]<br/>/p/&lt;profile&gt;/v1/runs"]
    ag["<b>Agent loop</b><br/>[Container: Hermes core, profile]<br/>model + toolsets"]
    ft["<b>call_frontend_tool</b><br/>[Container: Hermes gateway plugin agui_frontend]<br/>enabled only on &lt;profile&gt;"]
  end

  llm["<b>LLM</b><br/>[External system]"]

  user --> spa
  spa -->|"info / run / connect / stop<br/>HTTPS + SSE (AG-UI)"| edge
  edge -->|"HTTP + SSE"| hp
  hp -->|"read / write"| ts
  hp -->|"start run, events, stop<br/>HTTP + SSE, API key"| api
  api --> ag
  ag -->|"completions"| llm
  ag -->|"calls tool"| ft
  ft -->|"register call, poll result<br/>loopback HTTP, token"| hp
  spa -.->|"Create / Save<br/>(human click only)"| edge

  classDef person fill:#08427b,stroke:#052e56,color:#ffffff
  classDef container fill:#1168bd,stroke:#0b4884,color:#ffffff
  classDef ext fill:#6b6b6b,stroke:#4d4d4d,color:#ffffff
  class user person
  class spa,hp,ts,api,ag,ft container
  class edge,llm ext
  style P1 fill:none,stroke:#888,stroke-dasharray:5 5
  style P2 fill:none,stroke:#888,stroke-dasharray:5 5
  style P3 fill:none,stroke:#888,stroke-dasharray:5 5
```

## C4 level 3: components inside the Host plugin

```mermaid
flowchart TB
  spa["<b>Host SPA</b><br/>[Container: browser]"]
  api["<b>Hermes API server</b><br/>[Container: gateway process]"]
  ft["<b>call_frontend_tool</b><br/>[Container: gateway plugin]"]

  subgraph HP["Host plugin  [Hermes dashboard plugin, runs in the dashboard process]"]
    sf["<b>Surface</b><br/>[Component: host code]<br/>authorize, build_instructions,<br/>frontend tool schemas"]
    subgraph LIB["hermes_agui  [Python library, imported]"]
      rt["<b>agui router</b><br/>[Component: runtime.py]<br/>routes: info, run,<br/>connect, stop<br/>headers first,<br/>heartbeat every 10 s"]
      tr["<b>Translator</b><br/>[Component: translate.py]<br/>Hermes events to AG-UI events"]
      br["<b>Broker</b><br/>[Component: broker.py]<br/>pending frontend calls,<br/>schema check, late results rejected"]
      hc["<b>Hermes client</b><br/>[Component: hermes_client.py]"]
      ts[("<b>Transcript store</b><br/>[Component: store.py, SQLite]")]
    end
  end

  spa -->|"run / connect / stop<br/>SSE"| rt
  rt -->|"authorize, instructions"| sf
  rt -->|"frames in, events out"| tr
  tr -->|"name + args for a call"| br
  rt -->|"resolve result, drop on stop"| br
  rt -->|"start, events, stop"| hc
  hc -->|"HTTP + SSE"| api
  rt -->|"append, replay"| ts
  ft -->|"broker/call, broker/result<br/>loopback, token"| br

  classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
  classDef container fill:#1168bd,stroke:#0b4884,color:#ffffff
  class sf,rt,tr,br,hc,ts component
  class spa,api,ft container
  style HP fill:none,stroke:#888,stroke-dasharray:5 5
  style LIB fill:none,stroke:#1168bd
```

## Flows

Each participant is a box from the diagrams above, grouped by the process it runs in.

### 1. A plain chat turn

```mermaid
sequenceDiagram
  autonumber
  box rgba(120,120,120,0.12) Browser
    participant SPA as Host SPA
  end
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant RT as agui router
    participant SF as Surface
    participant TR as Translator
    participant TS as Transcript store
  end
  box rgba(8,66,123,0.12) Hermes gateway process
    participant API as Hermes API server
    participant AG as Agent loop
  end

  SPA->>RT: POST run (threadId, runId, messages, context)
  RT-->>SPA: response headers + RUN_STARTED at once
  RT->>SF: authorize, then build_instructions
  RT->>API: POST /v1/runs (session_id, new message only, instructions)
  API->>AG: run the turn
  loop while the model writes
    AG-->>API: text
    API-->>RT: message.delta
    RT->>TR: translate
    TR-->>SPA: TEXT_MESSAGE_START / CONTENT
  end
  Note over RT,SPA: ": heartbeat" every 10 s while quiet
  API-->>RT: run.completed
  TR-->>SPA: TEXT_MESSAGE_END, RUN_FINISHED
  RT->>TS: append user + assistant messages
```

The browser re-sends the whole history on every run. The agui router sends the Hermes API server only the new last
message, because Hermes keeps the session itself. The per-turn instructions carry live state (form values,
ownership, allowed tools) and Hermes never stores them.

### 2. A frontend tool call: one Hermes turn, two browser runs

```mermaid
sequenceDiagram
  autonumber
  box rgba(120,120,120,0.12) Browser
    participant SPA as Host SPA
  end
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant RT as agui router
    participant TR as Translator
    participant BR as Broker
  end
  box rgba(8,66,123,0.12) Hermes gateway process
    participant API as Hermes API server
    participant AG as Agent loop
    participant FT as call_frontend_tool
  end

  SPA->>RT: POST run (run 1)
  RT-->>SPA: RUN_STARTED
  RT->>BR: open session with the Surface's tools
  RT->>API: POST /v1/runs
  API->>AG: run the turn
  AG->>FT: call_frontend_tool("set_fields", args)
  FT->>BR: POST broker/call (session, call id, name, args)
  Note over BR: checks args against<br/>the Surface schema
  API-->>RT: tool.started (call id only, no name or args)
  RT->>TR: translate
  TR->>BR: wait for call (session, call id)
  BR-->>TR: name + args
  TR-->>SPA: TOOL_CALL_START / ARGS / END
  TR-->>SPA: RUN_FINISHED (run 1 ends, the turn keeps waiting)
  loop every 1 s
    FT->>BR: GET broker/result
    BR-->>FT: 204 pending
  end
  Note over SPA: tool handler fills the fields<br/>and builds per-field results
  SPA->>RT: POST run (run 2), last message = tool result
  RT-->>SPA: RUN_STARTED
  RT->>BR: resolve(session, call id, result)
  FT->>BR: GET broker/result
  BR-->>FT: 200 result
  FT-->>AG: tool result
  AG-->>API: reply text
  API-->>RT: message.delta, run.completed (same stream as run 1)
  RT->>TR: translate
  TR-->>SPA: TEXT_MESSAGE_*, RUN_FINISHED
```

If the args are invalid, `broker/call` answers 422. call_frontend_tool then returns the errors to the Agent loop, and
nothing reaches the browser.

### 3. The agent asks, the user clicks

```mermaid
sequenceDiagram
  autonumber
  actor U as User
  box rgba(120,120,120,0.12) Browser
    participant SPA as Host SPA
  end
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant RT as agui router
    participant BR as Broker
  end
  box rgba(8,66,123,0.12) Hermes gateway process
    participant FT as call_frontend_tool
  end

  FT->>BR: broker/call ask_choice(question, options)
  RT-->>SPA: TOOL_CALL_* ask_choice, RUN_FINISHED
  SPA->>U: shows the choices
  Note over FT,BR: keeps polling every 1 s, up to 300 s
  U->>SPA: clicks an option
  SPA->>RT: POST run with the tool result
  RT->>BR: resolve
  BR-->>FT: 200 choice
  Note over SPA,RT: if the user types instead, the call resolves<br/>as user_replied and the message becomes the next turn
```

### 4. Stop

```mermaid
sequenceDiagram
  autonumber
  box rgba(120,120,120,0.12) Browser
    participant SPA as Host SPA
  end
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant RT as agui router
    participant BR as Broker
  end
  box rgba(8,66,123,0.12) Hermes gateway process
    participant API as Hermes API server
    participant FT as call_frontend_tool
  end

  SPA->>RT: POST stop/{threadId}
  RT->>API: POST /v1/runs/{id}/stop
  RT->>BR: drop session
  FT->>BR: GET broker/result
  BR-->>FT: 410 dropped
  Note over FT: sees the Hermes interrupt,<br/>exits within about 1 s
  RT-->>SPA: stopped = true, open text closed, run ended
  Note over BR: a late or duplicate result for that call is rejected
```

### 5. Reload: the chat comes back

```mermaid
sequenceDiagram
  autonumber
  box rgba(120,120,120,0.12) Browser
    participant SPA as Host SPA
  end
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant RT as agui router
    participant TS as Transcript store
  end

  Note over SPA: restores threadId + form draft<br/>from localStorage
  SPA->>RT: POST connect (threadId)
  RT->>TS: read messages for the thread
  RT-->>SPA: RUN_STARTED, MESSAGES_SNAPSHOT, RUN_FINISHED
  Note over SPA,RT: no transcript means an empty run and a fresh chat
```

### 6. The browser never answers

```mermaid
sequenceDiagram
  autonumber
  box rgba(17,104,189,0.12) Hermes dashboard process
    participant BR as Broker
  end
  box rgba(8,66,123,0.12) Hermes gateway process
    participant FT as call_frontend_tool
    participant AG as Agent loop
  end

  loop every 1 s for up to 300 s
    FT->>BR: GET broker/result
    BR-->>FT: 204 pending
  end
  FT-->>AG: status no_response
  Note over AG: the agent carries on, e.g. asks in chat
```

300 s is the default `timeout_s`, which stays under Hermes' 420 s per-tool limit.

## Security model

- **The Surface decides access.** Every route calls `authorize` with the host's own session and CSRF checks. The
  browser's `context` is prompt data only and never grants access.
- **Frontend tool args are checked on the server** against the Surface's schemas. CopilotKit's zod-to-JSON-schema
  conversion is lossy, so the server schemas win.
- **The Broker's HTTP routes are loopback-only** and need a bearer token: `AGUI_BROKER_TOKEN` in the profile `.env`,
  compared in constant time. The Edge proxy must never expose `broker/*`.
- **The agent never commits.** Create and Save are human clicks in the Host SPA. Frontend tools only change the form
  in the browser.
- **Session ids carry the Surface id**, so threads can't collide across surfaces or profiles.

## Edge proxy requirements

- Response headers and `RUN_STARTED` go out at once. Some auth proxies return 502 when headers take more than about 30 s.
- The 10 s heartbeat must stay under the shortest idle timeout on the path: nginx defaults to 60 s, and CDNs commonly
  allow about 100–125 s.
- Run and connect paths need proxy buffering off and a long read timeout. Allow-list exact paths; stop needs a pattern
  match because of `{threadId}`.

## Install

Both pieces are pinned to the same commit. The host plugin declares `hermes-agui` as a pinned git dependency, and the
gateway plugin is installed on the serving profile. Details and the packaging options tested are in
[`docs/packaging.md`](docs/packaging.md); the design history is in [`docs/design/`](docs/design/).
