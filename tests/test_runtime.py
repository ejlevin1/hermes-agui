"""Runtime router, Hermes HTTP client and transcript store, against fakes (synthetic data only).

Request bodies mirror the real CopilotKit 1.90 captures of S8 (fixtures/slim/run1-request, run2-request,
connect-request, stop-requests).
"""

import asyncio
import json
import pathlib

import httpx
import pytest
from fastapi import FastAPI

from hermes_agui import FrontendToolSpec, Principal, Surface, SurfaceRegistry
from hermes_agui.hermes_client import HermesError, HttpHermesClient
from hermes_agui.runtime import HEARTBEAT_S, make_router
from hermes_agui.store import InMemoryTranscriptStore, SqliteTranscriptStore

pytestmark = pytest.mark.anyio

RID = "run_h1"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# -- fakes ---------------------------------------------------------------------------------------------------------


class FakeHermes:
    """start_run hands out run ids; events(run) yields what the test pushes with ``push`` (None = stream drops)."""

    def __init__(self):
        self.started = []
        self.stopped = []
        self.queues = {}
        self.start_gate = None  # asyncio.Event: start_run blocks until set
        self.n = 0

    async def start_run(self, *, profile, session_id, input_text, instructions):
        if self.start_gate is not None:
            await self.start_gate.wait()
        self.n += 1
        rid = f"run_h{self.n}"
        self.started.append(
            {
                "profile": profile,
                "session_id": session_id,
                "input_text": input_text,
                "instructions": instructions,
                "run": rid,
            }
        )
        self.queues[rid] = asyncio.Queue()
        return rid

    async def events(self, *, profile, run_id):
        q = self.queues[run_id]
        while True:
            f = await q.get()
            if f is None:
                return
            yield f
            if f.get("event", "").startswith("run."):
                return

    async def stop(self, *, profile, run_id):
        self.stopped.append(run_id)
        self.push(run_id, {"event": "run.cancelled", "run_id": run_id})

    def push(self, rid, *frames):
        for f in frames:
            self.queues[rid].put_nowait(f)


class FakeBroker:
    def __init__(self):
        self.opened = []
        self.dropped = []
        self.calls = {}  # (sid, id) -> {"name","args"}
        self.resolved = []
        self.alive = set()

    def open_session(self, session_id, tools):
        self.opened.append((session_id, [t.name for t in tools]))
        self.alive.add(session_id)

    def register(self, sid, tcid, name, args):
        self.calls[(sid, tcid)] = {"name": name, "args": args}

    async def wait_call(self, session_id, tool_call_id, timeout):
        return self.calls.get((session_id, tool_call_id))

    def resolve(self, session_id, tool_call_id, content):
        if session_id not in self.alive or (session_id, tool_call_id) not in self.calls:
            return False
        if any(r[:2] == (session_id, tool_call_id) for r in self.resolved):
            return False
        self.resolved.append((session_id, tool_call_id, content))
        return True

    def drop_session(self, session_id):
        self.dropped.append(session_id)
        self.alive.discard(session_id)


TOOLS = (
    FrontendToolSpec(
        "set_fields",
        "set form fields",
        {"type": "object", "properties": {"values": {"type": "object"}}},
    ),
    FrontendToolSpec("ask_choice", "ask", {"type": "object"}, mode="human"),
)


class DemoSurface(Surface):
    profile = "demo-profile"
    title = "Demo form"
    frontend_tools = TOOLS

    def __init__(self, sid="default", allow=("u1",)):
        self.id = sid
        self.allow = set(allow)

    def authorize(self, request):
        u = request.headers.get("x-user")
        return Principal(u, display=u) if u in self.allow else None

    def build_instructions(self, ctx):
        values = [json.loads(c["value"]) for c in ctx.run_input.get("context", [])]
        return f"INSTR thread={ctx.thread_id} ctx={json.dumps(values)}"


class Env:
    def __init__(self, **kw):
        self.hermes = FakeHermes()
        self.broker = FakeBroker()
        self.store = InMemoryTranscriptStore()
        self.registry = SurfaceRegistry()
        self.registry.register(DemoSurface())
        self.registry.register(DemoSurface("other", allow=("nobody",)))
        app = FastAPI()
        router = make_router(
            self.registry, self.hermes, self.broker, store=self.store, **kw
        )
        app.include_router(router, prefix="/agui")
        self.app = app
        transport = httpx.ASGITransport(app=app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://t")
        self.sid = "agui:default:u1:t1"

    def req(self, method, path, **kw):
        return self.client.request(
            method, "/agui/" + path, headers={"x-user": "u1"}, **kw
        )

    async def run(
        self, messages, thread="t1", run="r1", path="agent/default/run", context=()
    ):
        body = {
            "threadId": thread,
            "runId": run,
            "protocolVersion": "1.0",
            "state": {},
            "messages": messages,
            "tools": [],
            "context": list(context),
            "forwardedProps": {},
        }
        r = await self.req("POST", path, json=body)
        assert r.status_code == 200, r.text
        return parse(r.text), r.text


def parse(text):
    return [json.loads(b[6:]) for b in text.split("\n\n") if b.startswith("data: ")]


def kinds(evs):
    return [e["type"] for e in evs]


def delta(text, rid=RID):
    return {"event": "message.delta", "run_id": rid, "delta": text}


def done(rid=RID, kind="run.completed", **kw):
    return {"event": kind, "run_id": rid, **kw}


def user(text, i="u-1"):
    return {"id": i, "role": "user", "content": text}


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.fixture
async def env():
    e = Env()
    yield e
    await e.client.aclose()


async def run_task(env, messages, **kw):
    return asyncio.create_task(env.run(messages, **kw))


async def wait_started(env, n=1):
    for _ in range(200):
        if len(env.hermes.started) >= n:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("start_run not called")


# -- info / auth ---------------------------------------------------------------------------------------------------


async def test_info_minimal_and_filtered(env):
    r = await env.req("GET", "info")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "sse" and body["audioFileTranscriptionEnabled"] is False
    assert list(body["agents"]) == ["default"]  # "other" denies u1
    assert body["agents"]["default"]["description"] == "Demo form"
    for banned in ("licenseStatus", "intelligence", "a2ui"):
        assert banned not in body


async def test_unknown_agent_404_and_denied_403(env):
    r = await env.req("POST", "agent/nope/run", json={})
    assert r.status_code == 404
    for path in (
        "agent/other/run",
        "agent/other/connect",
        "agent/other/stop/t1",
        "agui/other",
    ):
        r = await env.req("POST", path, json={})
        assert r.status_code == 403, path
    r = await env.client.post("/agui/agent/default/run", json={})  # no principal at all
    assert r.status_code == 403
    assert env.hermes.started == []


# -- plain turn ----------------------------------------------------------------------------------------------------


async def test_text_turn_sequence_and_hermes_call(env):
    ctx = [{"description": "form", "value": '{"title": ""}'}]
    t = await run_task(env, [user("hello")], context=ctx)
    await wait_started(env)
    env.hermes.push("run_h1", delta("Hel"), delta("lo"), done(output="Hello"))
    evs, raw = await t
    assert kinds(evs) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    (call,) = env.hermes.started
    assert call["profile"] == "demo-profile"
    assert call["session_id"] == "agui:default:u1:t1"  # surface-prefixed (S9 3.7)
    assert call["input_text"] == "hello"
    assert call["instructions"] == 'INSTR thread=t1 ctx=[{"title": ""}]'
    assert env.broker.opened == [("agui:default:u1:t1", ["set_fields", "ask_choice"])]
    assert env.broker.dropped == ["agui:default:u1:t1"]  # turn over


async def test_only_trailing_user_message_is_sent_to_hermes(env):
    history = [
        user("first", "u-1"),
        {"id": "a1", "role": "assistant", "content": "hi"},
        user("second", "u-2"),
    ]
    t = await run_task(env, history)
    await wait_started(env)
    env.hermes.push("run_h1", done(output="ok"))
    await t
    assert [c["input_text"] for c in env.hermes.started] == ["second"]


async def test_plain_agui_route_is_the_same_handler(env):
    t = await run_task(env, [user("hello")], path="agui/default")
    await wait_started(env)
    env.hermes.push("run_h1", delta("x"), done(output="x"))
    evs, _ = await t
    assert kinds(evs)[0] == "RUN_STARTED" and kinds(evs)[-1] == "RUN_FINISHED"


async def test_text_closed_before_run_error_and_before_cancel_finish(env):
    t = await run_task(env, [user("a")])
    await wait_started(env)
    env.hermes.push("run_h1", delta("par"), done(kind="run.failed", error="boom"))
    evs, _ = await t
    assert kinds(evs)[-3:] == ["TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "RUN_ERROR"]
    assert evs[-1]["message"] == "boom"

    t = await run_task(env, [user("b")], thread="t2", run="r2")
    await wait_started(env, 2)
    env.hermes.push("run_h2", delta("par"), done("run_h2", kind="run.cancelled"))
    evs, _ = await t
    assert kinds(evs)[-2:] == ["TEXT_MESSAGE_END", "RUN_FINISHED"]


async def test_backend_tool_progress(env):
    t = await run_task(env, [user("a")])
    await wait_started(env)
    env.hermes.push(
        "run_h1",
        delta("Checking. "),
        {
            "event": "tool.started",
            "tool": "inventory_search",
            "tool_call_id": "b1",
            "preview": "",
        },
        {
            "event": "tool.completed",
            "tool": "inventory_search",
            "tool_call_id": "b1",
            "error": False,
        },
        done(output="Checking. "),
    )
    evs, _ = await t
    assert kinds(evs) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "RUN_FINISHED",
    ]
    assert evs[4]["toolCallName"] == "inventory_search" and evs[5]["delta"] == "{}"


async def test_hermes_stream_ending_without_terminal_is_an_error(env):
    t = await run_task(env, [user("a")])
    await wait_started(env)
    env.hermes.push("run_h1", delta("x"), None)
    evs, _ = await t
    assert kinds(evs)[-2:] == ["TEXT_MESSAGE_END", "RUN_ERROR"]


async def test_start_run_failure_is_run_error(env):
    async def boom(**kw):
        raise HermesError("503")

    env.hermes.start_run = boom
    evs, _ = await env.run([user("a")])
    assert kinds(evs) == ["RUN_STARTED", "RUN_ERROR"]
    assert env.broker.dropped  # session cleaned up


async def test_no_trailing_message_is_an_empty_run(env):
    evs, _ = await env.run([{"id": "a", "role": "assistant", "content": "hi"}])
    assert kinds(evs) == ["RUN_STARTED", "RUN_FINISHED"]
    assert env.hermes.started == []


# -- frontend tool: one Hermes turn across two AG-UI runs -----------------------------------------------------------


def fe_started(tcid="cf1", rid=RID):
    return {
        "event": "tool.started",
        "run_id": rid,
        "tool": "call_frontend_tool",
        "tool_call_id": tcid,
        "preview": "",
    }


async def frontend_run1(env):
    env.broker.register(env.sid, "cf1", "set_fields", {"values": {"title": "Dune"}})
    t = await run_task(env, [user("fill it")])
    await wait_started(env)
    env.hermes.push("run_h1", delta("Filling. "), fe_started())
    return await t


def run2_messages(content='{"title":"ok","bogus":"unknown_field"}'):
    call = {
        "id": "cf1",
        "type": "function",
        "function": {"name": "set_fields", "arguments": "{}"},
    }
    return [
        user("fill it"),
        {"id": "a1", "role": "assistant", "toolCalls": [call]},
        {"id": "tr-cf1", "role": "tool", "content": content, "toolCallId": "cf1"},
    ]


async def test_frontend_call_ends_run1_and_run2_resumes_same_hermes_run(env):
    evs, _ = await frontend_run1(env)
    assert kinds(evs) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "RUN_FINISHED",
    ]
    assert (
        evs[4]["toolCallName"] == "set_fields" and evs[4]["toolCallId"] == "cf1"
    )  # name from the broker
    assert json.loads(evs[5]["delta"]) == {"values": {"title": "Dune"}}
    assert (
        env.hermes.stopped == [] and env.broker.dropped == []
    )  # the Hermes run keeps waiting
    t = await run_task(env, run2_messages(), run="r2")
    await settle()
    assert env.broker.resolved == [
        (env.sid, "cf1", {"title": "ok", "bogus": "unknown_field"})
    ]
    env.hermes.push(
        "run_h1",
        {
            "event": "tool.completed",
            "tool": "call_frontend_tool",
            "tool_call_id": "cf1",
            "error": False,
        },
        delta("Done."),
        done(output="Done."),
    )
    evs2, _ = await t
    assert kinds(evs2) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert evs2[0]["runId"] == "r2"
    assert len(env.hermes.started) == 1  # same Hermes run, no second start_run
    assert env.broker.dropped == [env.sid]


async def test_rejected_result_is_run_error(env):
    await frontend_run1(env)
    t = await run_task(env, run2_messages(), run="r2")
    await settle()
    evs, _ = await env.run(run2_messages(), run="r3")  # duplicate result
    assert (
        kinds(evs) == ["RUN_STARTED", "RUN_ERROR"]
        and evs[1]["code"] == "tool_result_rejected"
    )
    env.hermes.push("run_h1", done(output=""))
    await t


async def test_result_for_unknown_call_rejected(env):
    evs, _ = await env.run(run2_messages())
    assert kinds(evs) == ["RUN_STARTED", "RUN_ERROR"]
    assert env.hermes.started == [] and env.broker.resolved == []


async def test_user_message_while_call_pending_resolves_user_replied_and_queues(env):
    await frontend_run1(env)
    t = await run_task(
        env, [user("fill it"), user("never mind, do X", "u-2")], run="r2"
    )
    await settle()
    assert env.broker.resolved == [(env.sid, "cf1", {"status": "user_replied"})]
    assert len(env.hermes.started) == 1  # queued behind the live run
    env.hermes.push("run_h1", delta("ok. "), done(output="ok. "))
    await wait_started(env, 2)
    assert env.hermes.started[1]["input_text"] == "never mind, do X"
    env.hermes.push("run_h2", delta("X done"), done("run_h2", output="X done"))
    evs, _ = await t
    assert (
        kinds(evs).count("RUN_STARTED") == 1 and kinds(evs)[-1] == "RUN_FINISHED"
    )  # still one AG-UI run
    assert (
        kinds(evs).count("TEXT_MESSAGE_START")
        == kinds(evs).count("TEXT_MESSAGE_END")
        == 2
    )


async def test_new_user_message_while_run_streaming_is_refused(env):
    t = await run_task(env, [user("a")])
    await wait_started(env)
    await settle()
    evs, _ = await env.run([user("b", "u-2")], run="r2")
    assert evs[-1]["code"] == "run_in_progress"
    env.hermes.push("run_h1", done(output=""))
    await t


async def test_stale_pending_run_is_reaped():
    e = Env(pending_ttl_s=0.0)
    try:
        e.broker.register(e.sid, "cf1", "set_fields", {})
        t = await run_task(e, [user("x")])
        await wait_started(e)
        e.hermes.push("run_h1", fe_started())
        await t
        await asyncio.sleep(0.01)
        evs, _ = await e.run(
            run2_messages(), run="r2"
        )  # reaped before the result arrives
        assert kinds(evs) == ["RUN_STARTED", "RUN_ERROR"]
        assert e.hermes.stopped == ["run_h1"]
    finally:
        await e.client.aclose()


# -- stop ----------------------------------------------------------------------------------------------------------


async def test_stop_during_stream_stops_hermes_drops_session_and_closes_text(env):
    t = await run_task(env, [user("a")])
    await wait_started(env)
    env.hermes.push("run_h1", delta("par"))
    await settle()
    r = await env.req("POST", "agent/default/stop/t1", json={"runId": "r1"})
    assert r.json() == {"stopped": True}
    evs, _ = await t
    assert kinds(evs)[-3:] == [
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert env.hermes.stopped == ["run_h1"] and env.sid in env.broker.dropped


async def test_stop_while_frontend_call_pending_rejects_late_result(env):
    await frontend_run1(env)
    r = await env.req("POST", "agent/default/stop/t1", json={"runId": "r1"})
    assert r.json() == {"stopped": True}
    assert env.hermes.stopped == ["run_h1"] and env.sid in env.broker.dropped
    evs, _ = await env.run(run2_messages(), run="r2")
    assert kinds(evs) == ["RUN_STARTED", "RUN_ERROR"] and env.broker.resolved == []


async def test_stop_unknown_thread(env):
    r = await env.req("POST", "agent/default/stop/nothing", json={})
    assert r.json() == {"stopped": False}


async def test_stop_during_start_run(env):
    env.hermes.start_gate = asyncio.Event()
    t = await run_task(env, [user("a")])
    await settle()
    await env.req("POST", "agent/default/stop/t1", json={})
    evs, _ = await t
    assert kinds(evs) == ["RUN_STARTED", "RUN_FINISHED"]
    assert env.hermes.started == []


# -- connect / transcript ------------------------------------------------------------------------------------------


async def test_connect_replays_transcript_as_messages_snapshot(env):
    await frontend_run1(env)
    t = await run_task(env, run2_messages(), run="r2")
    await settle()
    env.hermes.push("run_h1", delta("Done."), done(output="Done."))
    await t
    body = {"threadId": "t1", "runId": "c1", "messages": [], "tools": []}
    r = await env.req("POST", "agent/default/connect", json=body)
    evs = parse(r.text)
    assert kinds(evs) == ["RUN_STARTED", "MESSAGES_SNAPSHOT", "RUN_FINISHED"]
    msgs = evs[1]["messages"]
    assert [m["role"] for m in msgs] == [
        "user",
        "assistant",
        "assistant",
        "tool",
        "assistant",
    ]
    assert msgs[2]["toolCalls"][0]["function"]["name"] == "set_fields"
    assert msgs[-1]["content"] == "Done."


async def test_connect_unknown_thread_is_empty_run(env):
    r = await env.req(
        "POST", "agent/default/connect", json={"threadId": "nope", "runId": "c1"}
    )
    assert kinds(parse(r.text)) == ["RUN_STARTED", "RUN_FINISHED"]


async def test_transcript_is_per_principal(env):
    t = await run_task(env, [user("mine")])
    await wait_started(env)
    env.hermes.push("run_h1", done(output="x"))
    await t
    assert env.store.messages("agui:default:u2:t1") == []
    assert env.store.messages(env.sid)[0]["content"] == "mine"


# -- edge behaviour: headers + RUN_STARTED at once, heartbeat, client disconnect -------------------------------------


def test_default_heartbeat_within_edge_limits():
    assert 0 < HEARTBEAT_S <= 15


async def test_heartbeat_comments_while_hermes_is_silent():
    e = Env(heartbeat_s=0.03)
    try:
        t = await run_task(e, [user("a")])
        await wait_started(e)
        await asyncio.sleep(0.2)  # silent Hermes
        e.hermes.push("run_h1", delta("x"), done(output="x"))
        evs, raw = await t
        assert raw.count(": heartbeat") >= 3
        assert kinds(evs)[-1] == "RUN_FINISHED"
    finally:
        await e.client.aclose()


async def test_heartbeat_while_start_run_is_slow():
    e = Env(heartbeat_s=0.03)
    try:
        e.hermes.start_gate = asyncio.Event()
        t = await run_task(e, [user("a")])
        await asyncio.sleep(0.15)
        e.hermes.start_gate.set()
        await wait_started(e)
        e.hermes.push("run_h1", done(output=""))
        _, raw = await t
        assert raw.count(": heartbeat") >= 2
    finally:
        await e.client.aclose()


async def raw_asgi(app, path, body):
    """Drive the ASGI app by hand to see chunks as they are sent (httpx's ASGITransport buffers the body)."""
    sent, recv = [], asyncio.Queue()
    recv.put_nowait(
        {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    )
    headers = [(b"x-user", b"u1"), (b"content-type", b"application/json")]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }

    async def send(m):
        sent.append(m)

    return asyncio.create_task(app(scope, recv.get, send)), sent, recv


RUN_BODY = {
    "threadId": "t1",
    "runId": "r1",
    "messages": [user("a")],
    "tools": [],
    "context": [],
}


async def test_headers_and_run_started_before_hermes_responds(env):
    env.hermes.start_gate = asyncio.Event()  # start_run blocks
    task, sent, _ = await raw_asgi(env.app, "/agui/agent/default/run", RUN_BODY)
    for _ in range(100):
        if any(b"RUN_STARTED" in m.get("body", b"") for m in sent):
            break
        await asyncio.sleep(0.01)
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200
    hdrs = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert (
        hdrs["content-type"].startswith("text/event-stream")
        and hdrs["x-accel-buffering"] == "no"
    )
    assert any(b"RUN_STARTED" in m.get("body", b"") for m in sent)
    assert env.hermes.started == []  # Hermes has not even been called yet
    env.hermes.start_gate.set()
    await wait_started(env)
    env.hermes.push("run_h1", done(output=""))
    await asyncio.wait_for(task, 2)


async def test_client_disconnect_abandons_hermes_run(env):
    task, sent, recv = await raw_asgi(env.app, "/agui/agent/default/run", RUN_BODY)
    await wait_started(env)
    env.hermes.push("run_h1", delta("par"))
    await settle(50)
    recv.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 2)
    assert env.hermes.stopped == ["run_h1"] and env.sid in env.broker.dropped


# -- real CopilotKit request bodies (S8 fixtures, when the sibling repo is checked out) -----------------------------

FIXTURES = pathlib.Path.home() / "Development/hermes-items/spikes/s8/fixtures/slim"


@pytest.mark.skipif(not FIXTURES.exists(), reason="S8 fixtures not on this host")
async def test_real_copilotkit_bodies(env):
    def load(n):
        d = json.loads((FIXTURES / n).read_text())
        return d.get("body", d)

    t = asyncio.create_task(
        env.req("POST", "agent/default/run", json=load("run1-request.json"))
    )
    await wait_started(env)
    assert env.hermes.started[0]["input_text"] == "please fill the form"
    assert env.hermes.started[0]["instructions"].startswith("INSTR thread=")
    env.hermes.push("run_h1", done(output="ok"))
    assert kinds(parse((await t).text))[-1] == "RUN_FINISHED"
    r = await env.req(
        "POST", "agent/default/connect", json=load("connect-request.json")
    )
    assert kinds(parse(r.text))[0] == "RUN_STARTED"


# -- HttpHermesClient ----------------------------------------------------------------------------------------------


def sse_body(*frames):
    s = ": open\n\n"
    for i, f in enumerate(frames):
        s += f"id: {i}\ndata: {json.dumps(dict(f, seq=i))}\n\n: keepalive\n\n"
    return s


async def test_http_client_start_events_stop():
    seen = []

    def handler(req: httpx.Request):
        seen.append(
            (req.method, req.url.path, req.headers.get("authorization"), req.content)
        )
        if req.url.path == "/p/prof/v1/runs":
            return httpx.Response(202, json={"run_id": "run_9", "status": "started"})
        if req.url.path.endswith("/events"):
            body = sse_body(delta("a", "run_9"), done("run_9", output="a"))
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json={"status": "stopping"})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = HttpHermesClient("http://hermes:8642/", "k", client=mock)
    rid = await c.start_run(
        profile="prof", session_id="s1", input_text="hi", instructions="ctx"
    )
    assert rid == "run_9"
    assert json.loads(seen[0][3]) == {
        "input": "hi",
        "session_id": "s1",
        "instructions": "ctx",
    }
    assert seen[0][2] == "Bearer k"
    frames = [f async for f in c.events(profile="prof", run_id=rid)]
    assert [f["event"] for f in frames] == ["message.delta", "run.completed"]
    await c.stop(profile="prof", run_id=rid)
    assert seen[-1][:2] == ("POST", "/p/prof/v1/runs/run_9/stop")


async def test_http_client_errors_and_idempotent_stop():
    def handler(req):
        return (
            httpx.Response(404)
            if req.url.path.endswith("/stop")
            else httpx.Response(500, text="nope")
        )

    c = HttpHermesClient(
        "http://h", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(HermesError):
        await c.start_run(profile="p", session_id="s", input_text="x", instructions="")
    await c.stop(profile="p", run_id="gone")  # 404 is fine


async def test_http_client_reattaches_with_last_event_id():
    calls = []

    def handler(req):
        calls.append(req.headers.get("last-event-id"))
        if len(calls) == 1:  # dropped before the terminal frame
            return httpx.Response(200, text=sse_body(delta("a", "r")))
        return httpx.Response(
            200, text="id: 1\ndata: " + json.dumps(dict(done("r"), seq=1)) + "\n\n"
        )

    c = HttpHermesClient(
        "http://h", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    frames = [f async for f in c.events(profile="p", run_id="r")]
    assert [f["event"] for f in frames] == ["message.delta", "run.completed"]
    assert calls == [None, "0"]


# -- stores --------------------------------------------------------------------------------------------------------


def test_sqlite_store_roundtrip_and_persistence(tmp_path):
    p = tmp_path / "t.db"
    s = SqliteTranscriptStore(p)
    s.append("k1", {"id": "1", "role": "user", "content": "a"})
    s.append("k2", {"id": "2", "role": "user", "content": "other"})
    s.append("k1", {"id": "3", "role": "assistant", "content": "b"})
    assert [m["id"] for m in s.messages("k1")] == ["1", "3"]
    assert s.messages("nope") == []
    s.close()
    s2 = SqliteTranscriptStore(p)
    assert [m["id"] for m in s2.messages("k1")] == ["1", "3"]
    s2.close()
