"""Gateway tool secret lookup: profile scope (Hermes ``agent.secret_scope``) first, ``os.environ`` only without Hermes.

Fakes of ``agent.secret_scope`` cover the branches in-process; one test runs the real module of the e2e Hermes copy
(multiplex active, scope set / not set) in a subprocess.
"""

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOOD = {"tool_name": "fill_form", "args": {"title": "x"}}


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "agui_tool_secret_test", ROOT / "plugin" / "tool.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tool = _load_tool()


class UnscopedSecretError(RuntimeError):
    pass


def _fake_hermes(monkeypatch, get_secret):
    pkg = types.ModuleType("agent")
    mod = types.ModuleType("agent.secret_scope")
    mod.get_secret = get_secret
    pkg.secret_scope = mod
    monkeypatch.setitem(sys.modules, "agent", pkg)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", mod)


def _cfg(k, d=None):
    return {"broker_url": "http://127.0.0.1:9", "timeout_s": 1}.get(k, d)


def test_scope_set_returns_scoped_token(monkeypatch):
    monkeypatch.setenv(tool.TOKEN_ENV, "env-value-must-not-win")
    scope = {tool.TOKEN_ENV: "scoped-synthetic"}
    _fake_hermes(monkeypatch, lambda name, default=None: scope.get(name, default))
    assert tool.read_secret(tool.TOKEN_ENV) == "scoped-synthetic"


def test_not_cached_across_calls(monkeypatch):
    scope = {tool.TOKEN_ENV: "first"}
    _fake_hermes(monkeypatch, lambda name, default=None: scope.get(name, default))
    assert tool.read_secret(tool.TOKEN_ENV) == "first"
    scope[tool.TOKEN_ENV] = "second"
    assert tool.read_secret(tool.TOKEN_ENV) == "second"
    scope.clear()
    assert tool.read_secret(tool.TOKEN_ENV) == ""


def test_scope_miss_is_empty_not_env(monkeypatch):
    monkeypatch.setenv(tool.TOKEN_ENV, "other-profile-value")
    _fake_hermes(monkeypatch, lambda name, default=None: default)
    assert tool.read_secret(tool.TOKEN_ENV) == ""


def test_handler_passes_scoped_token_to_broker(monkeypatch):
    _fake_hermes(
        monkeypatch,
        lambda name, default=None: {tool.TOKEN_ENV: "scoped-synthetic"}.get(
            name, default
        ),
    )
    seen = {}

    def fake_run_call(args, *, broker_url, token, timeout_s):
        seen.update(broker_url=broker_url, token=token, timeout_s=timeout_s)
        return '{"status": "ok"}'

    monkeypatch.setattr(tool, "run_call", fake_run_call)
    assert json.loads(tool.make_handler(_cfg)(GOOD)) == {"status": "ok"}
    assert seen == {
        "broker_url": "http://127.0.0.1:9",
        "token": "scoped-synthetic",
        "timeout_s": 1.0,
    }


def test_multiplex_without_scope_is_error_result_not_crash(monkeypatch):
    monkeypatch.setenv(tool.TOKEN_ENV, "process-env-value")

    def raising(name, default=None):
        raise UnscopedSecretError(f"no scope for {name}")

    _fake_hermes(monkeypatch, raising)
    with pytest.raises(tool.SecretUnavailable) as ei:
        tool.read_secret(tool.TOKEN_ENV)
    assert "process-env-value" not in str(ei.value)
    out = tool.make_handler(_cfg)(GOOD)
    assert json.loads(out) == {
        "status": "error",
        "error": f"broker_url setting / {tool.TOKEN_ENV} not configured",
    }
    assert "process-env-value" not in out


def test_no_hermes_falls_back_to_env(monkeypatch):
    # None in sys.modules makes `import agent.secret_scope` raise ImportError
    monkeypatch.setitem(sys.modules, "agent", None)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setenv(tool.TOKEN_ENV, "env-synthetic")
    assert tool.read_secret(tool.TOKEN_ENV) == "env-synthetic"
    monkeypatch.delenv(tool.TOKEN_ENV)
    assert tool.read_secret(tool.TOKEN_ENV) == ""


# -- real agent.secret_scope (the e2e Hermes copy) ---------------------------------------------------------------
_CACHE = Path(
    os.environ.get("E2E_CACHE", Path.home() / ".cache" / "hermes-agui-e2e")
) / ("hermes-" + os.environ.get("HERMES_SHA", "84e3e72bbb"))
HERMES_PY = _CACHE / "venv" / "bin" / "python"

_PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("t", sys.argv[1]); t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
from agent import secret_scope as ss
cfg = lambda k, d=None: {"broker_url": "http://127.0.0.1:9", "timeout_s": 1}.get(k, d)
t.run_call = lambda args, *, broker_url, token, timeout_s: json.dumps({"token": token})
h = t.make_handler(cfg)
out = {"single": h({"tool_name": "x", "args": {}})}
ss.set_multiplex_active(True)
out["mux_unscoped"] = h({"tool_name": "x", "args": {}})
tok = ss.set_secret_scope({"AGUI_BROKER_TOKEN": "scoped-synthetic"})
try:
    out["mux_scoped"] = h({"tool_name": "x", "args": {}})
finally:
    ss.reset_secret_scope(tok)
print("RESULT", json.dumps(out))
"""


@pytest.mark.skipif(not HERMES_PY.exists(), reason=f"no Hermes venv at {HERMES_PY}")
def test_real_secret_scope_under_multiplex(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "fakehome").mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "fakehome"),
        "HERMES_HOME": str(home),
        "LANG": "en_US.UTF-8",
        "PYTHONPATH": str(_CACHE / "src"),
        "AGUI_BROKER_TOKEN": "process-env-synthetic",
    }
    out = subprocess.run(
        [str(HERMES_PY), "-c", _PROBE, str(ROOT / "plugin" / "tool.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),
    )
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, out.stdout[-800:] + out.stderr[-800:]
    res = {k: json.loads(v) for k, v in json.loads(line[0][7:]).items()}
    # single-profile: process env is the profile's env
    assert res["single"] == {"token": "process-env-synthetic"}
    # multiplex, no scope: Hermes fails closed -> error result, never the process env value
    assert res["mux_unscoped"]["status"] == "error"
    # multiplex with the profile scope: the scoped value, not the process env
    assert res["mux_scoped"] == {"token": "scoped-synthetic"}
