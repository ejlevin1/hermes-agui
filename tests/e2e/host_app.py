"""Tiny test host for the e2e suite: mounts hermes_agui's runtime + broker routers with two synthetic surfaces.

Run with the repo's uv env:  uv run --project <repo> python tests/e2e/host_app.py --port N --hermes-url URL ...
Also serves the built test app at /app/ and a debug log at /__debug/log (what the broker saw, per session).
"""

from __future__ import annotations

import argparse
import json
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from hermes_agui.broker import MemoryBroker, broker_router
from hermes_agui.hermes_client import HttpHermesClient
from hermes_agui.runtime import make_router
from hermes_agui.store import InMemoryTranscriptStore
from hermes_agui.surface import (
    FrontendToolSpec,
    Principal,
    Surface,
    SurfaceRegistry,
    TurnContext,
)

PROFILE = "agui-test"
LOG: list[dict] = []
_LOCK = threading.Lock()


def note(ev: str, **kw) -> None:
    with _LOCK:
        LOG.append({"t": time.time(), "ev": ev, **kw})


class LoggingBroker(MemoryBroker):
    def open_session(self, session_id, tools):
        note("open_session", session_id=session_id, tools=[t.name for t in tools])
        super().open_session(session_id, tools)

    def register_call(self, session_id, tool_call_id, name, args):
        try:
            r = super().register_call(session_id, tool_call_id, name, args)
        except Exception as e:
            note(
                "register_call",
                session_id=session_id,
                call=tool_call_id,
                name=name,
                error=type(e).__name__,
            )
            raise
        note(
            "register_call",
            session_id=session_id,
            call=tool_call_id,
            name=name,
            errors=r,
        )
        return r

    def resolve(self, session_id, tool_call_id, content):
        ok = super().resolve(session_id, tool_call_id, content)
        note(
            "resolve",
            session_id=session_id,
            call=tool_call_id,
            accepted=ok,
            content=content,
        )
        return ok

    def drop_session(self, session_id):
        note("drop_session", session_id=session_id)
        super().drop_session(session_id)


class LoggingHermes(HttpHermesClient):
    async def start_run(self, **kw):
        rid = await super().start_run(**kw)
        note("hermes_start", run_id=rid, session_id=kw["session_id"])
        return rid

    async def stop(self, *, profile, run_id):
        note("hermes_stop", run_id=run_id)
        await super().stop(profile=profile, run_id=run_id)


class TestSurface(Surface):
    profile = PROFILE
    __test__ = False

    def authorize(self, request):
        user = request.headers.get("x-test-user")
        return Principal(id=user, display=user) if user else None

    def build_instructions(self, ctx: TurnContext) -> str:
        note(
            "instructions",
            surface=self.id,
            session_id=ctx.session_id,
            thread=ctx.thread_id,
            run=ctx.run_id,
        )
        tools = {
            t.name: {"mode": t.mode, "parameters": t.parameters}
            for t in self.frontend_tools
        }
        return (
            f"SURFACE={self.id}. Use call_frontend_tool(tool_name, args) for UI actions. Tools: "
            + json.dumps(tools)
        )


class FormSurface(TestSurface):
    id = "test.form"
    title = "Test form"
    frontend_tools = (
        FrontendToolSpec(
            name="set_fields",
            description="Write values into the form; returns a per-field result.",
            parameters={
                "type": "object",
                "properties": {
                    "values": {
                        "type": "object",
                        "additionalProperties": {"type": ["string", "number"]},
                    },
                    "reason": {"type": "string"},
                },
                "required": ["values"],
                "additionalProperties": False,
            },
        ),
        FrontendToolSpec(
            name="ask_choice",
            description="Ask the user to pick one option; waits for the click.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["question", "options"],
            },
            mode="human",
        ),
    )


class OtherSurface(TestSurface):
    id = "test.other"
    title = "Other surface"
    frontend_tools = (
        FrontendToolSpec(
            name="other_tool",
            description="A tool that only exists on the other surface.",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        ),
    )


class StripProfile(httpx.AsyncBaseTransport):
    """The e2e Hermes gateway serves one profile on its own port (no /p/<profile>/ multiplexing): drop the prefix."""

    def __init__(self) -> None:
        self._t = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        prefix = f"/p/{PROFILE}"
        p = request.url.path
        if p.startswith(prefix):
            request.url = request.url.copy_with(
                raw_path=(
                    p[len(prefix) :]
                    + ("?" + request.url.query.decode() if request.url.query else "")
                ).encode()
            )
        return await self._t.handle_async_request(request)

    async def aclose(self):
        await self._t.aclose()


def build(args) -> FastAPI:
    registry = SurfaceRegistry()
    registry.register(FormSurface())
    registry.register(OtherSurface())
    broker = LoggingBroker()
    client = httpx.AsyncClient(
        transport=StripProfile(), timeout=httpx.Timeout(30.0, read=60.0)
    )
    hermes = LoggingHermes(args.hermes_url, args.api_key, client=client)
    app = FastAPI()
    app.include_router(
        make_router(
            registry,
            hermes,
            broker,
            store=InMemoryTranscriptStore(),
            heartbeat_s=args.heartbeat_s,
        ),
        prefix="/api/copilotkit",
    )
    app.include_router(broker_router(broker, token=args.token), prefix="/api/agui")

    @app.middleware("http")
    async def log_polls(request, call_next):
        resp = await call_next(request)
        if request.url.path.endswith("/broker/result"):
            note(
                "poll",
                session_id=request.query_params.get("session_id"),
                call=request.query_params.get("tool_call_id"),
                status=resp.status_code,
            )
        elif request.url.path.endswith("/broker/call"):
            note("broker_call_http", status=resp.status_code)
        return resp

    @app.get("/__debug/log")
    def debug_log():
        with _LOCK:
            return list(LOG)

    if args.static:
        app.mount("/app", StaticFiles(directory=args.static, html=True), name="app")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--hermes-url", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--static", default="")
    ap.add_argument("--heartbeat-s", type=float, default=15.0)
    args = ap.parse_args()
    uvicorn.run(build(args), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
