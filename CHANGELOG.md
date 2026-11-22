# Changelog

## 0.1.0 — first release

- **Surfaces**: `Surface` / `SurfaceRegistry` / `FrontendToolSpec` / `Principal` / `TurnContext`; a host app registers
  surfaces, per-surface auth and per-turn instructions.
- **CopilotKit runtime v2 REST + plain AG-UI router** (`make_router`): `info`, `agent/{id}/run|connect|stop/{thread}`,
  `agui/{id}`. Headers and `RUN_STARTED` sent at once, SSE comment heartbeat every ≤ 15 s, open text messages closed
  before `RUN_FINISHED`/`RUN_ERROR`, `connect` replays the thread as `MESSAGES_SNAPSHOT`.
- **Hermes API-server client** (`HttpHermesClient`) and Hermes → AG-UI event translation.
- **Broker** (`MemoryBroker`, `broker_router`): in-process frontend-tool broker plus a loopback-only, bearer-token
  gateway-facing router (`POST broker/call`, `GET broker/result`).
- **`call_frontend_tool`** gateway plugin (`plugin/`, toolset `agui_frontend`): validates args against the surface's
  schema, waits for the browser result (`no_response` after 300 s), honours interrupts.
- Surface/runtime/broker/gateway-tool/translate test suite, including the contract test.
