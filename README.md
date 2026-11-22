# hermes-agui

AG-UI / CopilotKit-runtime bridge and **frontend tools** for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- A host app (a Hermes dashboard plugin, e.g. hermes-items) registers **surfaces** (an agent-assisted form or flow).
- The browser uses CopilotKit v2 with `runtimeUrl` pointing at the host's bridge endpoint (CopilotKit runtime v2 REST:
  `info`, `agent/:id/run|connect|stop`) or any AG-UI client on the plain AG-UI route.
- The bridge drives the Hermes API server (`/v1/runs`) and translates its events to AG-UI.
- A native Hermes plugin tool `call_frontend_tool(tool_name, args)` lets the agent call tools the browser declared;
  the call blocks until the browser's next run carries the result.

Status: scaffold. Design and spike evidence: hermes-items `docs/spikes/S7.md`, `S8.md` and the agentic-forms plan.

## Develop
```
uv run -q -p 3.14 pytest -q
```
