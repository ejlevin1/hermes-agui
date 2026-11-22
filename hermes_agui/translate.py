"""Hermes ``/v1/runs`` frames -> AG-UI events (pure, synchronous, no I/O).

One :class:`Translator` serves one AG-UI run. Rules (S7 Q4, S8 section 3):

* ``message.delta`` -> ``TEXT_MESSAGE_START/CONTENT``; an open text message is always closed before any tool call,
  ``RUN_FINISHED`` or ``RUN_ERROR`` (the client rejects the sequence otherwise).
* backend ``tool.started`` -> ``TOOL_CALL_START`` + empty ``TOOL_CALL_ARGS`` + ``TOOL_CALL_END`` (Hermes frames carry no
  args: progress only); ``tool.completed`` -> ``TOOL_CALL_RESULT`` (no result text exists, so a status object).
* ``call_frontend_tool`` is not translated here: :meth:`Translator.feed` returns a :class:`FrontendCall` marker, the
  runtime asks the broker for name+args and calls :meth:`Translator.frontend_call`.
* ``run.completed|failed|cancelled|interrupted`` set :attr:`Translator.terminal`; the runtime then calls
  :meth:`Translator.close` (``RUN_FINISHED``/``RUN_ERROR``) unless it chains a queued Hermes run.

Messages worth keeping for the transcript are collected in :meth:`Translator.drain_messages`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from ag_ui.core import (
    BaseEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from .api import FRONTEND_TOOL

__all__ = ["FrontendCall", "Translator"]

_TERMINAL = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.interrupted": "interrupted",
}


@dataclass(frozen=True)
class FrontendCall:
    """Marker: Hermes started ``call_frontend_tool`` ``tool_call_id``; name+args must come from the broker."""

    tool_call_id: str


def _id() -> str:
    return uuid.uuid4().hex


class Translator:
    """Stateful translation of one Hermes run's frames inside one AG-UI run."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.terminal: str | None = None
        """``completed|failed|cancelled|interrupted`` once Hermes ended the run, else ``None``."""
        self.error: str = ""
        self._text_id: str | None = None
        self._buf: list[str] = []
        self._streamed = False  # any delta seen in this Hermes run
        self._backend: dict[
            str, str
        ] = {}  # tool_call_id -> tool name (started, not completed)
        self._messages: list[dict] = []

    # -- lifecycle -----------------------------------------------------------------------------------------------

    def start(self) -> list[BaseEvent]:
        return [RunStartedEvent(thread_id=self.thread_id, run_id=self.run_id)]

    def reset_segment(self) -> None:
        """Forget Hermes-run state before chaining another Hermes run into the same AG-UI run."""
        self.terminal = None
        self.error = ""
        self._streamed = False
        self._backend.clear()

    def close(self) -> list[BaseEvent]:
        """Close the open text message, then ``RUN_ERROR`` (Hermes failed) or ``RUN_FINISHED``."""
        out = self._close_text()
        if self.terminal == "failed":
            out.append(
                RunErrorEvent(
                    message=self.error or "agent run failed", code="hermes_run_failed"
                )
            )
        else:
            out.append(RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id))
        return out

    def fail(self, message: str, code: str = "bridge_error") -> list[BaseEvent]:
        """Bridge-side error: close text, ``RUN_ERROR``."""
        return [*self._close_text(), RunErrorEvent(message=message, code=code)]

    def suspend(self) -> list[BaseEvent]:
        """End this AG-UI run while the Hermes run keeps waiting (frontend tool call pending)."""
        return [
            *self._close_text(),
            RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id),
        ]

    def drain_messages(self) -> list[dict]:
        """AG-UI message dicts produced since the last call (for the transcript store)."""
        out, self._messages = self._messages, []
        return out

    # -- frames --------------------------------------------------------------------------------------------------

    def feed(self, frame: dict) -> list[BaseEvent | FrontendCall]:
        kind = frame.get("event")
        if kind == "message.delta":
            delta = frame.get("delta") or ""
            if not delta:
                return []
            self._streamed = True
            return self._text(delta)
        if kind == "tool.started":
            tcid = str(frame.get("tool_call_id") or _id())
            name = str(frame.get("tool") or "")
            if name == FRONTEND_TOOL:
                return [FrontendCall(tcid)]
            self._backend[tcid] = name
            out: list[BaseEvent | FrontendCall] = list(self._close_text())
            out += [
                ToolCallStartEvent(tool_call_id=tcid, tool_call_name=name),
                ToolCallArgsEvent(tool_call_id=tcid, delta="{}"),
                ToolCallEndEvent(tool_call_id=tcid),
            ]
            self._messages.append(_assistant_call(tcid, name, "{}"))
            return out
        if kind == "tool.completed":
            tcid = str(frame.get("tool_call_id") or "")
            if tcid not in self._backend:
                return []  # the frontend tool (result comes from the browser) or an unknown id
            del self._backend[tcid]
            content = json.dumps(
                {"status": "error" if frame.get("error") else "completed"}
            )
            mid = _id()
            self._messages.append(
                {"id": mid, "role": "tool", "content": content, "toolCallId": tcid}
            )
            return [
                ToolCallResultEvent(message_id=mid, tool_call_id=tcid, content=content)
            ]
        if kind in _TERMINAL:
            self.terminal = _TERMINAL[kind]
            out = []
            if not self._streamed and kind == "run.completed" and frame.get("output"):
                out += self._text(
                    str(frame["output"])
                )  # no deltas were streamed: surface the final output
            out += self._close_text()
            if kind in ("run.failed", "run.interrupted"):
                self.error = str(frame.get("error") or "")
            return out
        return []  # reasoning.available, replay.truncated, ...: not translated (reasoning UNVERIFIED)

    def frontend_call(self, tool_call_id: str, name: str, args: Any) -> list[BaseEvent]:
        """Events for a frontend tool call whose name+args the broker supplied."""
        arguments = (
            args
            if isinstance(args, str)
            else json.dumps(args if args is not None else {})
        )
        closing = (
            self._close_text()
        )  # records the assistant text before the call message
        self._messages.append(_assistant_call(tool_call_id, name, arguments))
        return [
            *closing,
            ToolCallStartEvent(tool_call_id=tool_call_id, tool_call_name=name),
            ToolCallArgsEvent(tool_call_id=tool_call_id, delta=arguments),
            ToolCallEndEvent(tool_call_id=tool_call_id),
        ]

    # -- internals -----------------------------------------------------------------------------------------------

    def _text(self, delta: str) -> list[BaseEvent]:
        out: list[BaseEvent] = []
        if self._text_id is None:
            self._text_id = _id()
            out.append(TextMessageStartEvent(message_id=self._text_id))
        self._buf.append(delta)
        out.append(TextMessageContentEvent(message_id=self._text_id, delta=delta))
        return out

    def _close_text(self) -> list[BaseEvent]:
        if self._text_id is None:
            return []
        mid, text = self._text_id, "".join(self._buf)
        self._text_id, self._buf = None, []
        self._messages.append({"id": mid, "role": "assistant", "content": text})
        return [TextMessageEndEvent(message_id=mid)]


def _assistant_call(tool_call_id: str, name: str, arguments: str) -> dict:
    return {
        "id": _id(),
        "role": "assistant",
        "toolCalls": [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }
