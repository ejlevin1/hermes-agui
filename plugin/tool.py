"""``call_frontend_tool`` handler: register the call with the dashboard broker, then poll for the browser's result.

Stdlib only (runs inside the Hermes venv). Hermes facts relied on (S7 Q2/Q3, hermes-items docs/spikes/S7.md):
session id and tool call id come from ``tools.approval_context`` (``_approval_session_id`` / ``_approval_tool_call_id``,
the same id the run events carry); ``tools.interrupt.is_interrupted()`` is the only way to notice ``/stop`` while
blocked; Hermes' default per-tool cap is 420 s, so we answer ``no_response`` at 300 s by default.

Config (Hermes policy: behaviour in config.yaml, secrets in the profile ``.env``):
``plugins.entries.agui_frontend.settings.broker_url`` (e.g. ``http://127.0.0.1:9119/api/plugins/<host>/agui``),
``...settings.timeout_s`` (default 300); secret ``AGUI_BROKER_TOKEN`` in the profile ``.env``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

TOKEN_ENV = "AGUI_BROKER_TOKEN"
DEFAULT_TIMEOUT_S = 300.0
HTTP_TIMEOUT_S = 10.0
_SLICE_S = 1.0

SCHEMA = {
    "name": "call_frontend_tool",
    "description": (
        "Run a tool in the user's browser (UI actions such as showing or filling a form) and return its result. "
        "The available frontend tools and their argument schemas are listed in this turn's instructions; "
        "`tool_name` must be one of them and `args` must match its schema."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "Name of the frontend tool to call.",
            },
            "args": {
                "type": "object",
                "description": "Arguments for that tool (see this turn's instructions).",
            },
        },
        "required": ["tool_name", "args"],
    },
}


def _ids() -> tuple[str, str]:
    from tools import approval_context as ac

    return ac._approval_session_id.get() or "", ac._approval_tool_call_id.get() or ""


def _is_interrupted() -> bool:
    from tools.interrupt import is_interrupted

    return is_interrupted()


def _request(
    method: str, url: str, token: str, body: dict | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw.decode(errors="replace")[:300]


def run_call(
    args: dict,
    *,
    broker_url: str,
    token: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    ids: Callable[[], tuple[str, str]] = _ids,
    interrupted: Callable[[], bool] = _is_interrupted,
) -> str:
    """Register the call, then poll for the result; returns the JSON string the model sees."""
    if (
        not isinstance(args, dict)
        or not isinstance(args.get("tool_name"), str)
        or not isinstance(args.get("args"), dict)
    ):
        return json.dumps(
            {"status": "error", "error": "expected {tool_name: string, args: object}"}
        )
    session_id, call_id = ids()
    if not session_id or not call_id:
        return json.dumps({"status": "error", "error": "no session/tool-call context"})
    base = broker_url.rstrip("/")
    try:
        code, body = _request(
            "POST",
            f"{base}/broker/call",
            token,
            {
                "session_id": session_id,
                "tool_call_id": call_id,
                "tool_name": args["tool_name"],
                "args": args["args"],
            },
        )
    except (OSError, ValueError) as e:
        return json.dumps({"status": "error", "error": f"broker unreachable: {e}"})
    if code == 422:
        errors = body.get("errors") if isinstance(body, dict) else body
        return json.dumps({"status": "invalid_arguments", "errors": errors})
    if code == 410:
        return json.dumps({"status": "cancelled"})
    if code != 200:
        return json.dumps({"status": "error", "error": f"broker returned {code}"})

    deadline = time.monotonic() + timeout_s
    q = urllib.parse.urlencode({"session_id": session_id, "tool_call_id": call_id})
    while True:
        if interrupted():
            return json.dumps({"status": "cancelled"})
        if time.monotonic() >= deadline:
            return json.dumps({"status": "no_response"})
        try:
            code, body = _request("GET", f"{base}/broker/result?{q}", token)
        except (OSError, ValueError):
            time.sleep(_SLICE_S)  # transient; the deadline still bounds us
            continue
        if code == 200 and isinstance(body, dict):
            res = body.get("result")
            return res if isinstance(res, str) else json.dumps(res)
        if code == 410:
            return json.dumps({"status": "cancelled"})
        if code != 204:
            time.sleep(_SLICE_S)


def make_handler(get_config: Callable[[str, Any], Any]) -> Callable[..., str]:
    def call_frontend_tool(args: dict, **_kwargs: Any) -> str:
        url = get_config("broker_url", "")
        token = os.environ.get(TOKEN_ENV, "")
        if not url or not token:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"broker_url setting / {TOKEN_ENV} not configured",
                }
            )
        try:
            timeout_s = float(get_config("timeout_s", DEFAULT_TIMEOUT_S))
        except (TypeError, ValueError):
            timeout_s = DEFAULT_TIMEOUT_S
        return run_call(args, broker_url=url, token=token, timeout_s=timeout_s)

    return call_frontend_tool
