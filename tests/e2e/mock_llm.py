"""Scripted OpenAI-compatible mock LLM for the e2e suite (adapted from hermes-items spikes/s7/mock_llm.py).

Scenario = marker `[[SC:<name>]]` found in the LAST user message; step = number of assistant tool-call
messages after that user message. Every request body is appended to $MOCK_LOG (jsonl)."""

import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("MOCK_LOG", "mock_requests.jsonl")
LOCK = threading.Lock()


def _text(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def fe(name, args):
    return ("tool", "call_frontend_tool", {"tool_name": name, "args": args})


def script(sc, step, last_tool):
    """-> list of ('reasoning'|'text'|'tool', ...) parts for this model call."""
    final = [("text", f"Done. The browser answered: {last_tool[:300]}")]
    if sc == "text":
        return [
            ("text", "Hello "),
            ("text", "from "),
            ("text", "the "),
            ("text", "mock."),
        ]
    if sc == "fill":
        if step == 0:
            return [
                ("text", "Filling the form. "),
                fe(
                    "set_fields",
                    {"values": {"title": "Dune", "year": 1965, "bogus": "x"}},
                ),
            ]
        return final
    if sc == "choice":
        if step == 0:
            return [
                ("text", "Let me ask. "),
                fe(
                    "ask_choice",
                    {"question": "Which type?", "options": ["Book", "Movie"]},
                ),
            ]
        return final
    if sc == "invalid":
        if step == 0:
            return [
                fe("set_fields", {"values": 5})
            ]  # violates our schema (values must be an object)
        return final
    if (
        sc == "slow"
    ):  # the model is silent for MOCK_SLOW_S before answering (heartbeat test)
        return [("text", "Finally answered.")]
    if sc == "other":
        if step == 0:
            return [fe("other_tool", {"note": "hi"})]
        return final
    if (
        sc == "crosscall"
    ):  # on test.other, ask for the OTHER surface's tool: must be rejected as unknown
        if step == 0:
            return [fe("set_fields", {"values": {"title": "x"}})]
        return final
    return [("text", "unknown scenario; tools offered: " + last_tool)]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(
                200,
                {"object": "list", "data": [{"id": "mock-model", "object": "model"}]},
            )
        self._send(404, {"error": "nf"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages", [])
        with LOCK, open(LOG, "a") as f:
            f.write(
                json.dumps({"t": time.time(), "path": self.path, "req": req}) + "\n"
            )
        last_user = max(
            (i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1
        )
        utext = _text(msgs[last_user]) if last_user >= 0 else ""
        sc = "unknown"
        if "[[SC:" in utext:
            sc = utext.split("[[SC:", 1)[1].split("]]", 1)[0]
        after = msgs[last_user + 1 :]
        step = sum(
            1 for m in after if m.get("role") == "assistant" and m.get("tool_calls")
        )
        last_tool = next(
            (_text(m) for m in reversed(after) if m.get("role") == "tool"), ""
        )
        if sc == "unknown":
            last_tool = ",".join(t["function"]["name"] for t in req.get("tools", []))
        if sc == "slow" and step == 0:
            time.sleep(float(os.environ.get("MOCK_SLOW_S", "40")))
        parts = script(sc, step, last_tool)
        mid = "chatcmpl-mock"
        tcs = [(i, p) for i, p in enumerate([p for p in parts if p[0] == "tool"])]
        if not req.get("stream"):
            text = "".join(p[1] for p in parts if p[0] == "text")
            msg = {"role": "assistant", "content": text or None}
            if tcs:
                msg["tool_calls"] = [
                    {
                        "id": f"call_mock_{sc}_{step}_{i}",
                        "type": "function",
                        "function": {"name": p[1], "arguments": json.dumps(p[2])},
                    }
                    for i, p in tcs
                ]
            return self._send(
                200,
                {
                    "id": mid,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "mock-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": msg,
                            "finish_reason": "tool_calls" if tcs else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def ch(delta, fin=None):
            d = json.dumps(
                {
                    "id": mid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "mock-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": fin}],
                }
            )
            data = f"data: {d}\n\n".encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        ch({"role": "assistant"})
        ti = 0
        for p in parts:
            if p[0] == "reasoning":
                ch({"reasoning_content": p[1]})
            elif p[0] == "text":
                ch({"content": p[1]})
            else:
                args = json.dumps(p[2])
                cid = f"call_mock_{sc}_{step}_{ti}"
                ch(
                    {
                        "tool_calls": [
                            {
                                "index": ti,
                                "id": cid,
                                "type": "function",
                                "function": {"name": p[1], "arguments": ""},
                            }
                        ]
                    }
                )
                for k in range(0, len(args), 256):  # split args across deltas
                    ch(
                        {
                            "tool_calls": [
                                {
                                    "index": ti,
                                    "function": {"arguments": args[k : k + 256]},
                                }
                            ]
                        }
                    )
                ti += 1
            time.sleep(0.02)
        ch({}, "tool_calls" if tcs else "stop")
        u = json.dumps(
            {
                "id": mid,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock-model",
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        for data in (f"data: {u}\n\n".encode(), b"data: [DONE]\n\n"):
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    port = int(sys.argv[1])
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
