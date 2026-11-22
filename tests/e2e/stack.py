"""Start/stop the e2e stack: mock LLM + Hermes gateway (profile agui-test, this repo's plugin/) + test host.

    python3 stack.py start <state.json> --static <dist> [--hermes-sha SHA]
    python3 stack.py stop  <state.json>

Everything lives under $E2E_WORK (default ~/hermes-agui-work/e2e), never ~/.hermes. Only PIDs recorded in the state
file are ever signalled. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
WORK = Path(
    os.environ.get("E2E_WORK", Path.home() / "hermes-agui-work" / "e2e")
).resolve()
SHA = os.environ.get("HERMES_SHA", "84e3e72bbb")
CACHE = (
    Path(
        os.environ.get("E2E_CACHE", Path.home() / ".cache" / "hermes-agui-e2e")
    ).resolve()
    / f"hermes-{SHA}"
)
PY = str(CACHE / "venv" / "bin" / "python")
PROFILE = "agui-test"
API_KEY = "e2e-synthetic-key"  # synthetic, local only
BROKER_TOKEN = "e2e-broker-token-synthetic"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, t: float, headers=None) -> None:
    end, last = time.time() + t, None
    while time.time() < end:
        try:
            urllib.request.urlopen(
                urllib.request.Request(url, headers=headers or {}), timeout=2
            ).read()
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4)
    raise SystemExit(f"{url} not up after {t}s: {last!r}")


def spawn(state: dict, tag: str, argv: list[str], env: dict, log: Path) -> None:
    f = open(log, "ab")
    p = subprocess.Popen(
        argv, env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True
    )
    state["pids"][tag] = p.pid


def hermes_env(home: Path, extra: dict | None = None) -> dict:
    fake = WORK / "fakehome"
    fake.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{CACHE}/venv/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(fake),
        "HERMES_HOME": str(home),
        "LANG": "en_US.UTF-8",
        "PYTHONPATH": str(CACHE / "src"),
    }
    env.update(extra or {})
    return env


def assert_home_safe(home: Path) -> None:
    """An unset/foreign HERMES_HOME would mean the real ~/.hermes (docs/packaging.md Gotchas): refuse."""
    code = "from hermes_constants import get_hermes_home;import sys;print(get_hermes_home().resolve())"
    out = subprocess.run(
        [PY, "-c", code],
        env=hermes_env(home),
        capture_output=True,
        text=True,
        cwd=str(WORK),
    )
    got = Path(out.stdout.strip() or "/nonexistent").resolve()
    if WORK not in got.parents and got != WORK:
        raise SystemExit(
            f"UNSAFE HERMES_HOME {got} (not under {WORK}): {out.stderr[-300:]}"
        )


def install_plugin(home: Path, state: dict) -> None:
    """Copy this repo's plugin/ into the profile (what `plugins install file://<repo>#plugin` yields, docs/packaging.md
    option 2). The real install command is NOT used here: on this sha it prepares a pm 'isolated runtime' and the
    gateway then re-execs into it WITHOUT aiohttp (API server never starts). Covered by spikes/import_proof instead."""
    dest = home / "plugins" / "agui_frontend"
    shutil.copytree(REPO / "plugin", dest, ignore=shutil.ignore_patterns("__pycache__"))
    state["plugin_install"] = (
        "copy of plugin/ into $HERMES_HOME/plugins/agui_frontend (not `hermes plugins install`)"
    )


def start(statefile: Path, static: str) -> None:
    if not (Path(PY).exists() and (CACHE / ".ready").exists()):
        raise SystemExit("hermes venv missing: run tests/e2e/setup_hermes.sh")
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "logs").mkdir(parents=True)
    state: dict = {"pids": {}, "work": str(WORK), "api_key": API_KEY}
    statefile.write_text(json.dumps(state))
    try:
        mock_port, hermes_port, host_port = free_port(), free_port(), free_port()
        mock_log = WORK / "logs" / "mock_requests.jsonl"
        home = WORK / "profiles" / PROFILE
        (home / "plugins").mkdir(parents=True)
        state.update(
            mock_port=mock_port,
            hermes_port=hermes_port,
            host_port=host_port,
            mock_log=str(mock_log),
            hermes_home=str(home),
        )
        spawn(
            state,
            "mock",
            [sys.executable, str(HERE / "mock_llm.py"), str(mock_port)],
            {
                "PATH": os.environ.get("PATH", ""),
                "MOCK_LOG": str(mock_log),
                "MOCK_SLOW_S": os.environ.get("MOCK_SLOW_S", "40"),
            },
            WORK / "logs" / "mock.log",
        )
        cfg = {
            "model": {
                "default": "mock-model",
                "provider": "custom",
                "base_url": f"http://127.0.0.1:{mock_port}/v1",
                "api_key": "mock-key-synthetic",
            },
            "platform_toolsets": {"api_server": ["agui_frontend"]},
            "plugins": {
                "enabled": ["agui_frontend"],
                "entries": {
                    "agui_frontend": {
                        "settings": {
                            "broker_url": f"http://127.0.0.1:{host_port}/api/agui",
                            "timeout_s": 300,
                        }
                    }
                },
            },
            "tools": {"tool_search": {"enabled": "off"}},
            "terminal": {"backend": "local"},
            "gateway": {"standalone": True},
        }
        (home / "config.yaml").write_text(
            json.dumps(cfg, indent=1)
        )  # JSON is valid YAML
        (home / ".env").write_text(f"AGUI_BROKER_TOKEN={BROKER_TOKEN}\n")
        assert_home_safe(home)
        install_plugin(home, state)
        # the installer may have rewritten config.yaml: re-assert our settings if the enable entry vanished
        spawn(
            state,
            "hermes",
            [PY, "-m", "hermes_cli.main", "gateway", "run"],
            hermes_env(
                home,
                {
                    "API_SERVER_ENABLED": "true",
                    "API_SERVER_KEY": API_KEY,
                    "API_SERVER_PORT": str(hermes_port),
                    "API_SERVER_HOST": "127.0.0.1",
                    "GATEWAY_ALLOW_ALL_USERS": "true",
                },
            ),
            WORK / "logs" / "hermes.log",
        )
        wait_http(f"http://127.0.0.1:{hermes_port}/health", 120)
        spawn(
            state,
            "host",
            [
                "uv",
                "run",
                "--project",
                str(REPO),
                "-q",
                "-p",
                "3.14",
                "python",
                str(HERE / "host_app.py"),
                "--port",
                str(host_port),
                "--hermes-url",
                f"http://127.0.0.1:{hermes_port}",
                "--api-key",
                API_KEY,
                "--token",
                BROKER_TOKEN,
                "--static",
                static,
            ],
            {**os.environ, "VIRTUAL_ENV": ""},
            WORK / "logs" / "host.log",
        )
        wait_http(f"http://127.0.0.1:{host_port}/__debug/log", 60)
    finally:
        statefile.write_text(json.dumps(state, indent=1))


def stop(statefile: Path) -> None:
    if not statefile.exists():
        return
    state = json.loads(statefile.read_text())
    pids = state.get("pids", {})
    for tag, pid in pids.items():
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(1.5)
    for tag, pid in pids.items():
        try:
            os.kill(pid, 0)
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    state["pids"] = {}
    statefile.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "stop"])
    ap.add_argument("state")
    ap.add_argument("--static", default="")
    a = ap.parse_args()
    (start(Path(a.state), a.static) if a.cmd == "start" else stop(Path(a.state)))
