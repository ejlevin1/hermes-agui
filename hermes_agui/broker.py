"""In-process frontend-tool broker and its gateway-facing loopback HTTP router.

``MemoryBroker`` is the rendezvous between the Hermes gateway tool ``call_frontend_tool`` (registers calls over
``POST broker/call``, polls ``GET broker/result``) and the runtime router (``wait_call`` / ``resolve``).

SECURITY: ``broker_router`` is gateway-facing. Bind/mount it on 127.0.0.1 only and never route it through the public
edge (proxy/oauth2-proxy/Cloudflare); the bearer ``token`` is a second line of defence, not the first.
"""

from __future__ import annotations

import asyncio
import hmac
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator

from .surface import FrontendToolSpec

__all__ = ["MemoryBroker", "broker_router"]

_RESULT_WAIT_S = 1.0


@dataclass
class _Call:
    name: str
    args: dict
    result: Any = None
    resolved: bool = False
    dropped: bool = False
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class _Session:
    tools: dict[str, FrontendToolSpec]
    calls: dict[str, _Call] = field(default_factory=dict)
    waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = field(
        default_factory=dict
    )
    dead: bool = False


class MemoryBroker:
    """Thread-safe broker (the router handlers run in a thread pool, ``wait_call`` on the event loop).

    A session that was dropped stays dead (tombstoned) so late registrations/results are rejected, until
    ``open_session`` is called again for the same id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}

    # -- Broker protocol -------------------------------------------------
    def open_session(self, session_id: str, tools: Sequence[FrontendToolSpec]) -> None:
        for t in tools:
            Draft202012Validator.check_schema(t.parameters)  # fail early, not as a 500
        with self._lock:
            old = self._sessions.get(session_id)
            if old is not None and not old.dead:
                old.tools = {
                    t.name: t for t in tools
                }  # refresh tools; keep pending calls
                return
            self._sessions[session_id] = _Session(tools={t.name: t for t in tools})

    async def wait_call(
        self, session_id: str, tool_call_id: str, timeout: float
    ) -> dict | None:
        loop = asyncio.get_running_loop()
        ev = asyncio.Event()
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.dead:
                return None
            c = s.calls.get(tool_call_id)
            if c is not None:
                return {"name": c.name, "args": c.args}
            s.waiters.setdefault(tool_call_id, []).append((loop, ev))
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except TimeoutError:
            pass
        finally:
            with self._lock:
                s = self._sessions.get(session_id)
                if s is not None:
                    ws = s.waiters.get(tool_call_id, [])
                    ws[:] = [w for w in ws if w[1] is not ev]
        with self._lock:
            s = self._sessions.get(session_id)
            c = s.calls.get(tool_call_id) if s and not s.dead else None
            return {"name": c.name, "args": c.args} if c else None

    def resolve(self, session_id: str, tool_call_id: str, content: Any) -> bool:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.dead:
                return False
            c = s.calls.get(tool_call_id)
            if c is None or c.resolved or c.dropped:
                return False
            c.result, c.resolved = content, True
        c.done.set()
        return True

    def drop_session(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = self._sessions[session_id] = _Session(tools={})
            s.dead = True
            calls = list(s.calls.values())
            waiters = [w for ws in s.waiters.values() for w in ws]
            s.waiters.clear()
        for c in calls:
            c.dropped = True
            c.done.set()
        for loop, ev in waiters:
            loop.call_soon_threadsafe(
                ev.set
            )  # wakes with no call -> wait_call returns None (cancel)

    # -- gateway-side operations (used by broker_router) ------------------
    def register_call(
        self, session_id: str, tool_call_id: str, name: str, args: dict
    ) -> list[str] | None:
        """Validate and register. Returns the error list (422) or ``None`` on success.

        Raises ``KeyError`` for an unknown/dead session, ``ValueError`` for a duplicate call id.
        """
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.dead:
                raise KeyError(session_id)
            spec = s.tools.get(name)
            if spec is None:
                self._wake_locked(s, tool_call_id)
                return [f"unknown frontend tool {name!r}; available: {sorted(s.tools)}"]
            if tool_call_id in s.calls:
                raise ValueError(tool_call_id)
        errors = sorted(
            f"{'/'.join(map(str, e.absolute_path)) or '<args>'}: {e.message}"
            for e in Draft202012Validator(spec.parameters).iter_errors(args)
        )
        if errors:
            with (
                self._lock
            ):  # rejected: wake wait_call (returns None) so the bridge stops waiting
                s = self._sessions.get(session_id)
                if s is not None:
                    self._wake_locked(s, tool_call_id)
            return errors
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None or s.dead:
                raise KeyError(session_id)
            if tool_call_id in s.calls:
                raise ValueError(tool_call_id)
            s.calls[tool_call_id] = _Call(name=name, args=args)
            waiters = list(s.waiters.get(tool_call_id, []))
        for loop, ev in waiters:
            loop.call_soon_threadsafe(ev.set)
        return None

    @staticmethod
    def _wake_locked(s: _Session, tool_call_id: str) -> None:
        """Wake (without a call) the waiters of ``tool_call_id``; caller holds the lock."""
        for loop, ev in s.waiters.get(tool_call_id, []):
            loop.call_soon_threadsafe(ev.set)

    def poll_result(
        self, session_id: str, tool_call_id: str, wait: float
    ) -> tuple[str, Any]:
        """('result', content) | ('pending', None) | ('dropped', None); waits up to ``wait`` seconds."""
        deadline = time.monotonic() + wait
        while True:
            with self._lock:
                s = self._sessions.get(session_id)
                c = s.calls.get(tool_call_id) if s else None
                if s is None or s.dead or c is None:
                    return "dropped", None
            if c.dropped:
                return "dropped", None
            remaining = deadline - time.monotonic()
            if c.done.wait(max(0.0, remaining)):
                if c.dropped:
                    return "dropped", None
                return "result", c.result
            return "pending", None


def broker_router(broker: MemoryBroker, *, token: str) -> APIRouter:
    """Gateway-facing router: ``POST broker/call`` and ``GET broker/result`` (loopback only, bearer token)."""
    if not token:
        raise ValueError("broker_router needs a non-empty token")
    mb = broker
    router = APIRouter()

    def check(authorization: str | None) -> None:
        got = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(401, "bad broker token")

    @router.post("/broker/call")
    def call(body: dict, authorization: str | None = Header(default=None)):
        check(authorization)
        try:
            sid, cid = str(body["session_id"]), str(body["tool_call_id"])
            name, args = str(body["tool_name"]), body["args"]
        except KeyError as e:
            raise HTTPException(400, f"missing field {e}") from None
        if not isinstance(args, dict):
            return JSONResponse({"errors": ["args must be an object"]}, status_code=422)
        try:
            errors = mb.register_call(sid, cid, name, args)
        except KeyError:
            return JSONResponse(
                {"error": "session closed", "status": "dropped"}, status_code=410
            )
        except ValueError:
            return JSONResponse({"error": "duplicate tool_call_id"}, status_code=409)
        if errors:
            return JSONResponse({"errors": errors}, status_code=422)
        return {"ok": True}

    @router.get("/broker/result")
    def result(
        session_id: str,
        tool_call_id: str,
        authorization: str | None = Header(default=None),
    ):
        check(authorization)
        kind, content = mb.poll_result(session_id, tool_call_id, _RESULT_WAIT_S)
        if kind == "result":
            return {"result": content}
        if kind == "pending":
            return Response(status_code=204)
        return JSONResponse({"status": "dropped"}, status_code=410)

    return router
