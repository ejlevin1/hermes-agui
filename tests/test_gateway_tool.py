"""Gateway tool: handler vs a real broker_router on uvicorn (no Hermes), plus loading the plugin in a live-sha Hermes."""

import importlib.util
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from hermes_agui.broker import MemoryBroker, broker_router
from hermes_agui.surface import FrontendToolSpec

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "t-synthetic-token"
SID = "agui:s:u:t1"
SPEC = FrontendToolSpec(
    name="fill_form",
    description="fill",
    parameters={
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    },
)
GOOD = {"tool_name": "fill_form", "args": {"title": "x"}}


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "agui_tool_under_test", ROOT / "plugin" / "tool.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tool = _load_tool()


@pytest.fixture
def server():
    broker = MemoryBroker()
    broker.open_session(SID, [SPEC])
    app = FastAPI()
    app.include_router(broker_router(broker, token=TOKEN))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield broker, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)


def run(url, args, *, timeout_s=5, interrupted=lambda: False, cid="c1", token=TOKEN):
    return tool.run_call(
        args,
        broker_url=url,
        token=token,
        timeout_s=timeout_s,
        ids=lambda: (SID, cid),
        interrupted=interrupted,
    )


def test_result_roundtrip(server):
    broker, url = server
    threading.Timer(0.5, broker.resolve, (SID, "c1", json.dumps({"ok": 1}))).start()
    assert json.loads(run(url, GOOD)) == {"ok": 1}


def test_validation_errors_returned_to_model(server):
    _, url = server
    out = json.loads(run(url, {"tool_name": "fill_form", "args": {}}))
    assert out["status"] == "invalid_arguments" and any(
        "title" in e for e in out["errors"]
    )
    assert (
        json.loads(run(url, {"tool_name": "zzz", "args": {}}))["status"]
        == "invalid_arguments"
    )
    assert json.loads(run(url, {"tool_name": "fill_form"}))["status"] == "error"


def test_no_response_at_timeout(server):
    _, url = server
    t0 = time.monotonic()
    assert json.loads(run(url, GOOD, timeout_s=1.5)) == {"status": "no_response"}
    assert time.monotonic() - t0 < 4


def test_interrupt_exits_within_1_5s(server):
    _, url = server
    flag = {"v": False}
    threading.Timer(0.7, lambda: flag.update(v=True)).start()
    t0 = time.monotonic()
    assert json.loads(run(url, GOOD, timeout_s=60, interrupted=lambda: flag["v"])) == {
        "status": "cancelled"
    }
    assert time.monotonic() - t0 - 0.7 < 1.5


def test_drop_session_cancels(server):
    broker, url = server
    threading.Timer(0.5, broker.drop_session, (SID,)).start()
    t0 = time.monotonic()
    assert json.loads(run(url, GOOD, timeout_s=60)) == {"status": "cancelled"}
    assert time.monotonic() - t0 < 3


def test_bad_token_and_unreachable_broker(server):
    _, url = server
    assert json.loads(run(url, GOOD, token="wrong"))["status"] == "error"
    assert json.loads(run("http://127.0.0.1:9", GOOD))["status"] == "error"


def test_make_handler_needs_config(monkeypatch):
    monkeypatch.delenv(tool.TOKEN_ENV, raising=False)
    h = tool.make_handler(lambda k, d=None: d)
    assert json.loads(h(GOOD))["status"] == "error"


# -- real Hermes (live sha) ---------------------------------------------------------------------------------------
HERMES_PY = (
    Path(
        os.environ.get(
            "HERMES_AGUI_BROKER_VENV", Path.home() / "hermes-agui-work/broker/venv"
        )
    )
    / "bin/python"
)

_PROBE = r"""
import json
from hermes_cli.plugins import get_plugin_manager
m = get_plugin_manager(); m.discover_and_load()
from tools.registry import registry
e = registry.get_entry("call_frontend_tool", scope=m.scope_key)
print("RESULT", json.dumps({"found": e is not None, "toolset": getattr(e, "toolset", None),
      "params": sorted(((e.schema or {}).get("parameters") or {}).get("properties", {})) if e else None}))
"""


@pytest.mark.skipif(not HERMES_PY.exists(), reason=f"no Hermes venv at {HERMES_PY}")
def test_plugin_registers_in_live_sha_hermes(tmp_path):
    home = tmp_path / "home" / "profiles" / "p"
    (home / "plugins").mkdir(parents=True)
    os.symlink(ROOT / "plugin", home / "plugins" / "agui_frontend")
    cfg = {
        "plugins": {
            "enabled": ["agui_frontend"],
            "entries": {
                "agui_frontend": {"settings": {"broker_url": "http://127.0.0.1:1"}}
            },
        }
    }
    (home / "config.yaml").write_text(json.dumps(cfg))
    (tmp_path / "fakehome").mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "fakehome"),
        "HERMES_HOME": str(home),
        "LANG": "en_US.UTF-8",
        "AGUI_BROKER_TOKEN": "x",
    }
    out = subprocess.run(
        [str(HERMES_PY), "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, out.stdout[-800:] + out.stderr[-800:]
    res = json.loads(line[0][7:])
    assert (
        res["found"]
        and res["toolset"] == "agui_frontend"
        and res["params"] == ["args", "tool_name"]
    ), res
