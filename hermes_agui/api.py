"""Protocols the runtime router depends on: the Hermes API-server client, the frontend-tool broker and the transcript
store. Concrete implementations live in their own modules (or in the host app); tests use fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, runtime_checkable

from .surface import FrontendToolSpec

__all__ = ["FRONTEND_TOOL", "Broker", "HermesClient", "TranscriptStore"]

FRONTEND_TOOL = "call_frontend_tool"
"""Name of the native Hermes tool (toolset ``agui_frontend``) that forwards a call to the browser."""


@runtime_checkable
class HermesClient(Protocol):
    """Client for the Hermes API server of one or more profiles (``/v1/runs``)."""

    async def start_run(
        self, *, profile: str, session_id: str, input_text: str, instructions: str
    ) -> str:
        """Start a Hermes run (``POST /v1/runs``, ``session_id`` in the body) and return its run id."""
        ...

    def events(self, *, profile: str, run_id: str) -> AsyncIterator[dict]:
        """Stream the run's ``/v1/runs/{id}/events`` frames as decoded dicts until the run ends.

        Frames carry text deltas and ``tool.started``/``tool.completed`` with ``tool_call_id`` only (no args, results
        or reasoning; S7). Implement as an ``async def`` generator.
        """
        ...

    async def stop(self, *, profile: str, run_id: str) -> None:
        """Stop the run (``POST /v1/runs/{id}/stop``); idempotent."""
        ...


@runtime_checkable
class Broker(Protocol):
    """Rendezvous between the gateway tool (which registers calls) and the browser (which returns results).

    In-process in the dashboard; the gateway reaches it over 127.0.0.1 (see ``broker_router``).
    """

    def open_session(self, session_id: str, tools: Sequence[FrontendToolSpec]) -> None:
        """Accept calls for ``session_id``, restricted to ``tools`` (their schemas are authoritative)."""
        ...

    async def wait_call(
        self, session_id: str, tool_call_id: str, timeout: float
    ) -> dict | None:
        """Wait until the gateway registered ``tool_call_id``; return ``{"name", "args"}`` or ``None`` on timeout."""
        ...

    def resolve(self, session_id: str, tool_call_id: str, content: Any) -> bool:
        """Deliver the browser result. ``False`` = unknown/dead session or call, or a duplicate (rejected)."""
        ...

    def drop_session(self, session_id: str) -> None:
        """Forget the session; pending and later results for it are rejected."""
        ...


@runtime_checkable
class TranscriptStore(Protocol):
    """Per-thread AG-UI message history, replayed by ``connect`` as ``MESSAGES_SNAPSHOT`` (D7)."""

    def append(self, thread_key: str, message: dict) -> None:
        """Append one AG-UI message dict (``id``, ``role``, ...) to the thread."""
        ...

    def messages(self, thread_key: str) -> list[dict]:
        """All messages of the thread in order; ``[]`` for an unknown thread."""
        ...
