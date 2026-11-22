# hermes-agui

AG-UI / CopilotKit-runtime bridge and **frontend tools** for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

- A host app (a Hermes dashboard plugin, e.g. hermes-items) registers **surfaces** (an agent-assisted form or flow).
- The browser uses CopilotKit v2 with `runtimeUrl` pointing at the host's bridge endpoint (CopilotKit runtime v2 REST:
  `info`, `agent/:id/run|connect|stop`) or any AG-UI client on the plain AG-UI route.
- The bridge drives the Hermes API server (`/v1/runs`) and translates its events to AG-UI.
- A native Hermes plugin tool `call_frontend_tool(tool_name, args)` lets the agent call tools the browser declared;
  the call blocks until the browser's next run carries the result.

Status: **0.1.0** (first release). See `CHANGELOG.md`. Design and spike evidence: `docs/design/`, hermes-items
`docs/spikes/S7.md`, `S8.md`.

How it works (C4 + sequence diagrams): [ARCHITECTURE.md](ARCHITECTURE.md)

## Install (host app)
Two halves, both pinned to the same commit; details and caveats in `docs/packaging.md`.
```sh
# gateway half, on each profile that serves agentic surfaces
hermes plugins install "https://github.com/ejlevin1/hermes-agui.git#plugin" --ref <sha> --enable
# host plugin: declare hermes-agui in ITS pyproject.toml (pm installs it into the managed env)
#   dependencies = ["hermes-agui"]
#   [tool.uv.sources]
#   hermes-agui = { git = "ssh://git@github.com/ejlevin1/hermes-agui.git", rev = "<40-char sha>" }
hermes plugins install <host plugin source> --enable
hermes gateway restart        # and restart the dashboard
```
Configure the gateway plugin: `plugins.entries.agui_frontend.settings.broker_url` (the loopback URL of the host's
broker router) and `AGUI_BROKER_TOKEN` in the profile `.env`. The `https://`/`ssh://` install forms are UNVERIFIED
(only `file://` was proven, see `docs/packaging.md`).

## Minimal host example
```python
from fastapi import APIRouter
from hermes_agui import FrontendToolSpec, Principal, Surface, SurfaceRegistry
from hermes_agui.broker import MemoryBroker, broker_router
from hermes_agui.hermes_client import HttpHermesClient
from hermes_agui.runtime import make_router
from hermes_agui.store import InMemoryTranscriptStore


class Demo(Surface):
    id, profile, title = "demo", "demo-profile", "Demo form"
    frontend_tools = [FrontendToolSpec("set_field", "Set a form field",
                      {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                       "required": ["name", "value"]})]

    def authorize(self, request):                      # return None to deny
        return Principal(id="user-1")

    def build_instructions(self, ctx):
        return "You help fill in the form. Use set_field to edit it."


registry = SurfaceRegistry()
registry.register(Demo())
broker = MemoryBroker()
hermes = HttpHermesClient("http://127.0.0.1:8642", api_key="<hermes api key>")

router = APIRouter()   # the host plugin's dashboard router
router.include_router(make_router(registry, hermes, broker, store=InMemoryTranscriptStore()), prefix="/agui")
# gateway-facing: see the loopback rule below
router.include_router(broker_router(broker, token="<AGUI_BROKER_TOKEN>"), prefix="/agui")
```
Point CopilotKit v2 `runtimeUrl` at `<host plugin base>/agui` (agent id = surface `id`).

## Edge rules
- **Headers first, heartbeat**: the run route sends headers and `RUN_STARTED` immediately and an SSE comment
  heartbeat every ≤ 15 s (oauth2-proxy 502s after 30 s without headers; nginx idles at 60 s; Cloudflare 125 s). Do not
  put a buffering layer in front.
- **Loopback-only broker**: `broker_router` is for the Hermes gateway only (bearer token). Never expose it through the
  public ingress/proxy; restrict `/agui/broker/*` to 127.0.0.1 at the edge.

## Develop
```
uv run -q -p 3.14 pytest -q
```
