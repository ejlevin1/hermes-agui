"""CopilotKit runtime-v2 REST routes + a plain AG-UI route, driven by a Hermes API server (S8 + S7).

Routes (relative to wherever the host mounts the router): ``GET info``, ``POST agent/{agent_id}/run|connect``,
``POST agent/{agent_id}/stop/{thread_id}``, ``POST agui/{agent_id}`` (same run handler).

One Hermes turn can span several AG-UI runs: run 1 ends at ``TOOL_CALL_END`` of a frontend call while the Hermes run
keeps waiting; run 2 carries the ``tool`` message, resolves the broker call and keeps streaming the *same* Hermes
event iterator. That state lives in :class:`_Active`, keyed by Hermes session id.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ag_ui.core import MessagesSnapshotEvent, RunFinishedEvent, RunStartedEvent
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api import Broker, HermesClient, TranscriptStore
from .surface import Surface, SurfaceRegistry, TurnContext
from .translate import FrontendCall, Translator

__all__ = ["HEARTBEAT_S", "make_router"]

log = logging.getLogger(__name__)

HEARTBEAT_S = 10.0
"""SSE comment heartbeat interval: oauth2-proxy 502s after 30 s without headers, nginx idles at 60 s (S9-C)."""

_HB = b": heartbeat\n\n"
_END = object()  # Hermes event stream exhausted


@dataclass
class _Active:
    """A Hermes run that is still in flight for a session (possibly between two AG-UI runs)."""

    session_id: str
    profile: str
    run_id: str | None = None
    iterator: AsyncIterator[dict] | None = None
    pending_call: str | None = None
    pending_since: float = 0.0
    queued: list[tuple[str, str]] = field(
        default_factory=list
    )  # (input text, instructions)
    streaming: bool = False
    stopped: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    next_task: asyncio.Task | None = None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # AG-UI multimodal parts
        return "\n".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def _decode(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def make_router(
    registry: SurfaceRegistry,
    hermes: HermesClient,
    broker: Broker,
    *,
    store: TranscriptStore,
    heartbeat_s: float = HEARTBEAT_S,
    broker_wait_s: float = 30.0,
    pending_ttl_s: float = 330.0,
) -> APIRouter:
    """Build the router. ``pending_ttl_s``: a Hermes run left waiting for run 2 longer than this is reaped
    (the gateway tool gives up at 300 s)."""
    router = APIRouter()
    enc = EventEncoder()
    active: dict[str, _Active] = {}

    def sse(ev) -> bytes:
        return enc.encode(ev).encode()

    # -- helpers -------------------------------------------------------------------------------------------------

    async def wait(aw, entry: _Active | None = None) -> AsyncIterator[Any]:
        """Await ``aw`` yielding ``_HB`` every ``heartbeat_s`` while it runs; the last item is ``(result,)``.

        If ``entry`` is stopped meanwhile the last item is ``(_END,)`` and ``aw`` is cancelled.
        """
        task = aw if isinstance(aw, asyncio.Future) else asyncio.ensure_future(aw)
        stop = asyncio.ensure_future(entry.stop_event.wait()) if entry else None
        try:
            while True:
                waiting = {task} | ({stop} if stop else set())
                done, _ = await asyncio.wait(
                    waiting, timeout=heartbeat_s, return_when=asyncio.FIRST_COMPLETED
                )
                if task in done:
                    yield (task.result(),)
                    return
                if stop is not None and stop in done:
                    task.cancel()
                    yield (_END,)
                    return
                yield _HB
        finally:
            if stop is not None:
                stop.cancel()

    async def next_frame(it: AsyncIterator[dict]):
        try:
            return await it.__anext__()
        except StopAsyncIteration:
            return _END

    async def close_iter(entry: _Active) -> None:
        if entry.next_task is not None and not entry.next_task.done():
            entry.next_task.cancel()
        entry.next_task = None
        it, entry.iterator = entry.iterator, None
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await aclose()

    async def teardown(entry: _Active, *, stop_hermes: bool) -> None:
        """Forget the run: wake waiters, drop the broker session, optionally stop Hermes, close the iterator."""
        entry.stopped = True
        entry.stop_event.set()
        if active.get(entry.session_id) is entry:
            del active[entry.session_id]
        broker.drop_session(entry.session_id)
        if stop_hermes and entry.run_id:
            with contextlib.suppress(Exception):
                await hermes.stop(profile=entry.profile, run_id=entry.run_id)
        if not entry.streaming:
            await close_iter(entry)

    async def reap() -> None:
        now = time.monotonic()
        for e in [
            e
            for e in active.values()
            if e.pending_call and now - e.pending_since > pending_ttl_s
        ]:
            log.info("reaping stale Hermes run %s for %s", e.run_id, e.session_id)
            await teardown(e, stop_hermes=True)

    async def body_of(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def resolve_surface(request: Request, agent_id: str):
        try:
            surface = registry.get(agent_id)
        except KeyError:
            return None, JSONResponse({"error": "unknown agent"}, status_code=404)
        principal = surface.authorize(request)
        if principal is None:
            return None, JSONResponse({"error": "forbidden"}, status_code=403)
        return (surface, principal), None

    def stream_response(gen: AsyncIterator[bytes]) -> StreamingResponse:
        return StreamingResponse(
            gen,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- routes --------------------------------------------------------------------------------------------------

    @router.get("/info")
    async def info(request: Request):
        agents = {
            s.id: {"name": s.id, "description": s.title, "className": "HermesAgent"}
            for s in registry.list_for(request)
        }
        return {
            "version": "hermes-agui",
            "audioFileTranscriptionEnabled": False,
            "mode": "sse",
            "agents": agents,
        }

    @router.post("/agent/{agent_id}/connect")
    async def connect(agent_id: str, request: Request):
        resolved, err = resolve_surface(request, agent_id)
        if err:
            return err
        surface, principal = resolved
        body = await body_of(request)
        thread_id = str(body.get("threadId") or "")
        run_id = str(body.get("runId") or "connect")
        messages = (
            store.messages(surface.session_id(principal, thread_id))
            if thread_id
            else []
        )

        async def gen():
            yield sse(RunStartedEvent(thread_id=thread_id, run_id=run_id))
            if messages:
                yield sse(MessagesSnapshotEvent(messages=messages))
            yield sse(RunFinishedEvent(thread_id=thread_id, run_id=run_id))

        return stream_response(gen())

    @router.post("/agent/{agent_id}/stop/{thread_id}")
    async def stop(agent_id: str, thread_id: str, request: Request):
        resolved, err = resolve_surface(request, agent_id)
        if err:
            return err
        surface, principal = resolved
        entry = active.get(surface.session_id(principal, thread_id))
        if entry is None:
            return {"stopped": False}
        await teardown(entry, stop_hermes=True)
        return {"stopped": True}

    @router.post("/agent/{agent_id}/run")
    async def run(agent_id: str, request: Request):
        return await handle_run(agent_id, request)

    @router.post("/agui/{agent_id}")
    async def agui(agent_id: str, request: Request):
        return await handle_run(agent_id, request)

    # -- the run handler -----------------------------------------------------------------------------------------

    async def handle_run(agent_id: str, request: Request):
        resolved, err = resolve_surface(request, agent_id)
        if err:
            return err
        surface, principal = resolved
        body = await body_of(request)
        return stream_response(drive(surface, principal, body))

    async def drive(surface: Surface, principal, body: dict) -> AsyncIterator[bytes]:
        thread_id = str(body.get("threadId") or "")
        run_id = str(body.get("runId") or "")
        sid = surface.session_id(principal, thread_id)
        tr = Translator(thread_id, run_id)
        for (
            ev
        ) in tr.start():  # headers are already out; RUN_STARTED before anything slow
            yield sse(ev)
        entry: _Active | None = None
        clean = False
        try:
            await reap()
            messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
            trailing_tools = []
            for m in reversed(messages):
                if m.get("role") != "tool":
                    break
                trailing_tools.insert(0, m)
            last = messages[-1] if messages else {}
            entry = active.get(sid)
            ctx = TurnContext(surface.id, principal, thread_id, run_id, sid, body)

            if trailing_tools:
                ids = {str(m.get("toolCallId")): m for m in trailing_tools}
                ok = (
                    entry is not None
                    and not entry.stopped
                    and entry.pending_call in ids
                )
                if ok:
                    ok = broker.resolve(
                        sid,
                        entry.pending_call,
                        _decode(ids[entry.pending_call].get("content")),
                    )
                if not ok:
                    for ev in tr.fail(
                        "frontend tool result rejected (unknown, late or duplicate call)",
                        "tool_result_rejected",
                    ):
                        yield sse(ev)
                    clean = True
                    return
                for m in trailing_tools:
                    store.append(
                        sid,
                        {
                            "id": m.get("id") or f"tool-{m.get('toolCallId')}",
                            "role": "tool",
                            "content": _text_of(m.get("content")),
                            "toolCallId": m.get("toolCallId"),
                        },
                    )
                entry.pending_call = None
            elif last.get("role") == "user":
                text = _text_of(last.get("content"))
                if entry is not None and entry.streaming:
                    for ev in tr.fail(
                        "another run is still streaming for this thread",
                        "run_in_progress",
                    ):
                        yield sse(ev)
                    clean = True
                    return
                instructions = surface.build_instructions(ctx)
                store.append(
                    sid,
                    {
                        "id": last.get("id") or f"user-{run_id}",
                        "role": "user",
                        "content": text,
                    },
                )
                if entry is not None and entry.pending_call is not None:
                    # user spoke while a frontend call is pending: answer it, queue the message behind the live run
                    broker.resolve(sid, entry.pending_call, {"status": "user_replied"})
                    entry.pending_call = None
                    entry.queued.append((text, instructions))
                else:
                    if entry is not None:  # stale leftover
                        await teardown(entry, stop_hermes=True)
                    entry = _Active(sid, surface.profile)
                    active[sid] = entry
                    broker.open_session(sid, surface.frontend_tools)
                    entry.queued.append((text, instructions))
            else:
                for ev in tr.suspend():  # nothing to do for this run
                    yield sse(ev)
                clean = True
                return

            entry.streaming = True
            while True:
                # start a queued Hermes run when none is live
                if entry.iterator is None:
                    if not entry.queued:
                        break
                    text, instructions = entry.queued.pop(0)
                    start = asyncio.ensure_future(
                        hermes.start_run(
                            profile=entry.profile,
                            session_id=sid,
                            input_text=text,
                            instructions=instructions,
                        )
                    )
                    started = None
                    async for item in wait(start, entry):
                        if item is _HB:
                            yield _HB
                        else:
                            started = item[0]
                    if started is _END or entry.stopped:
                        break
                    entry.run_id = started
                    entry.iterator = hermes.events(
                        profile=entry.profile, run_id=started
                    )
                    tr.reset_segment()

                if entry.next_task is None:
                    entry.next_task = asyncio.ensure_future(next_frame(entry.iterator))
                frame = None
                async for item in wait(entry.next_task, entry):
                    if item is _HB:
                        yield _HB
                    else:
                        frame = item[0]
                if frame is _END and entry.stopped:
                    break
                entry.next_task = None
                if frame is _END:  # Hermes closed the stream without a terminal frame
                    tr.terminal, tr.error = (
                        "failed",
                        "Hermes event stream ended unexpectedly",
                    )
                    frame = None
                items = tr.feed(frame) if frame else []
                for it in items:
                    if isinstance(it, FrontendCall):
                        call = None
                        w = asyncio.ensure_future(
                            broker.wait_call(sid, it.tool_call_id, broker_wait_s)
                        )
                        async for item in wait(w, entry):
                            if item is _HB:
                                yield _HB
                            else:
                                call = item[0]
                        if entry.stopped:
                            break
                        if not call:
                            log.warning(
                                "broker has no call %s for %s: skipped",
                                it.tool_call_id,
                                sid,
                            )
                            continue
                        for ev in tr.frontend_call(
                            it.tool_call_id, call["name"], call.get("args")
                        ):
                            yield sse(ev)
                        for m in tr.drain_messages():
                            store.append(sid, m)
                        entry.pending_call = it.tool_call_id
                        entry.pending_since = time.monotonic()
                        entry.streaming = False
                        for ev in tr.suspend():
                            yield sse(ev)
                        clean = True
                        return
                    yield sse(it)
                for m in tr.drain_messages():
                    store.append(sid, m)
                if entry.stopped:
                    break
                if tr.terminal:
                    await close_iter(entry)
                    entry.run_id = None
                    if entry.queued and tr.terminal in ("completed",):
                        continue
                    break

            # turn over (completed, failed, stopped or nothing left to run)
            closing = list(tr.close())
            # store before yielding: a client that disconnects on the final
            # event must not lose the reply it already saw (D7)
            for m in tr.drain_messages():
                store.append(sid, m)
            for ev in closing:
                yield sse(ev)
            entry.streaming = False
            await teardown(entry, stop_hermes=False)
            clean = True
        except (
            Exception
        ) as exc:  # bridge/Hermes failure: still end the stream with a valid sequence
            log.exception("run failed for %s", sid)
            if entry is not None:
                entry.streaming = False
                with contextlib.suppress(Exception):
                    await teardown(entry, stop_hermes=True)
            failing = list(tr.fail(f"{type(exc).__name__}: {exc}"))
            for m in tr.drain_messages():
                store.append(sid, m)
            for ev in failing:
                yield sse(ev)
            clean = True
        finally:
            if (
                not clean and entry is not None
            ):  # client went away mid-stream: abandon the Hermes run
                entry.streaming = False
                with contextlib.suppress(Exception):
                    tr.close()
                    for m in tr.drain_messages():
                        store.append(sid, m)
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(teardown(entry, stop_hermes=True))

    return router
