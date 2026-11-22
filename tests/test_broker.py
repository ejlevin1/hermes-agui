"""MemoryBroker semantics and the gateway-facing broker_router."""

import asyncio
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_agui.broker import MemoryBroker, broker_router
from hermes_agui.surface import FrontendToolSpec

TOKEN = "t-synthetic-token"
H = {"Authorization": f"Bearer {TOKEN}"}
FILL = FrontendToolSpec(
    name="fill_form",
    description="fill",
    parameters={
        "type": "object",
        "properties": {"title": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["title"],
        "additionalProperties": False,
    },
)
SID = "agui:s:u:t1"


@pytest.fixture
def broker():
    b = MemoryBroker()
    b.open_session(SID, [FILL])
    return b


@pytest.fixture
def client(broker):
    app = FastAPI()
    app.include_router(broker_router(broker, token=TOKEN))
    return TestClient(app)


def call(client, cid="c1", name="fill_form", args=None, sid=SID, headers=H):
    body = {
        "session_id": sid,
        "tool_call_id": cid,
        "tool_name": name,
        "args": {"title": "x"} if args is None else args,
    }
    return client.post("/broker/call", json=body, headers=headers)


def result(client, cid="c1", headers=H):
    return client.get(
        "/broker/result",
        params={"session_id": SID, "tool_call_id": cid},
        headers=headers,
    )


def test_wait_call_after_register(client, broker):
    assert call(client).status_code == 200
    assert asyncio.run(broker.wait_call(SID, "c1", 1)) == {
        "name": "fill_form",
        "args": {"title": "x"},
    }


def test_wait_call_wakes_when_registered_later(client, broker):
    async def go():
        t = asyncio.create_task(broker.wait_call(SID, "c1", 5))
        await asyncio.sleep(0.1)
        await asyncio.to_thread(call, client)
        return await t

    assert asyncio.run(go())["name"] == "fill_form"


def test_wait_call_timeout_and_unknown_session(broker):
    assert asyncio.run(broker.wait_call(SID, "nope", 0.1)) is None
    assert asyncio.run(broker.wait_call("other", "c1", 0.1)) is None


def test_resolve_roundtrip_then_duplicate_rejected(client, broker):
    call(client)
    assert broker.resolve(SID, "c1", "R1") is True
    r = result(client)
    assert r.status_code == 200 and r.json() == {"result": "R1"}
    assert broker.resolve(SID, "c1", "R2") is False  # duplicate
    assert result(client).json() == {"result": "R1"}  # first result kept


def test_resolve_unknown_rejected(client, broker):
    assert broker.resolve(SID, "never", "x") is False
    assert broker.resolve("other-session", "c1", "x") is False


def test_result_pending_204(client):
    call(client)
    assert result(client).status_code == 204


def test_drop_session_gives_410_and_rejects_late_results(client, broker):
    call(client)
    broker.drop_session(SID)
    assert result(client).status_code == 410
    assert broker.resolve(SID, "c1", "late") is False
    assert call(client, cid="c2").status_code == 410  # no new calls on a dead session
    broker.open_session(SID, [FILL])  # reopening revives the id with a clean slate
    assert call(client, cid="c3").status_code == 200


def test_drop_wakes_pending_result_poll_and_waiters(client, broker):
    call(client)
    out = {}
    th = threading.Thread(target=lambda: out.update(r=result(client)))
    th.start()

    async def go():
        t = asyncio.create_task(broker.wait_call(SID, "future", 5))
        await asyncio.sleep(0.1)
        broker.drop_session(SID)
        return await asyncio.wait_for(t, 1)

    assert asyncio.run(go()) is None  # cancelled, not a 5 s wait
    th.join(3)
    assert out["r"].status_code == 410


def test_result_wait_wakes_on_resolve(client, broker):
    call(client)
    threading.Timer(0.3, broker.resolve, (SID, "c1", "in-time")).start()
    r = result(client)
    assert r.status_code == 200 and r.json() == {"result": "in-time"}


@pytest.mark.parametrize(
    "kw,msg",
    [
        ({"args": {}}, "title"),
        ({"args": {"title": 3}}, "title"),
        ({"args": {"title": "x", "extra": 1}}, "extra"),
        ({"name": "nope"}, "unknown frontend tool"),
    ],
)
def test_call_validation_422(client, kw, msg):
    r = call(client, **kw)
    assert r.status_code == 422
    assert any(msg in e for e in r.json()["errors"])


def test_call_non_object_args_422(client):
    assert call(client, args=[1]).status_code == 422


def test_call_duplicate_id_409_unknown_session_410(client):
    assert call(client).status_code == 200
    assert call(client).status_code == 409
    assert call(client, sid="nobody").status_code == 410


def test_auth_required(client):
    assert call(client, headers={}).status_code == 401
    assert call(client, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert result(client, headers={}).status_code == 401


def test_empty_token_refused(broker):
    with pytest.raises(ValueError):
        broker_router(broker, token="")


def test_open_session_rejects_invalid_schema():
    bad = FrontendToolSpec(name="t", description="d", parameters={"type": 12})
    with pytest.raises(Exception):
        MemoryBroker().open_session("s", [bad])
