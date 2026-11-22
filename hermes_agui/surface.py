"""Surfaces: what a host app registers to expose an agentic UI through the bridge.

A *surface* is one CopilotKit agent (its ``id`` is the CopilotKit ``agentId``) served by one Hermes profile. The host
app subclasses :class:`Surface`, registers instances in a :class:`SurfaceRegistry` and hands the registry to the
runtime router. This module is pure data + defaults: no I/O, no web framework import.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["FrontendToolSpec", "Principal", "Surface", "SurfaceRegistry", "TurnContext"]

_MODES = ("sync", "human")


@dataclass(frozen=True)
class FrontendToolSpec:
    """A browser-side tool the agent may call through ``call_frontend_tool``.

    ``parameters`` is a JSON Schema object and is authoritative: the gateway tool validates the agent's args against
    it (the schema a CopilotKit client derives from zod is lossy, so it is never trusted). ``mode`` is ``"sync"`` for
    tools the browser answers immediately and ``"human"`` for tools that wait for a person (e.g. a confirm dialog).

    Note: ``parameters`` is a dict, so instances compare by value but are not hashable.
    """

    name: str
    description: str
    parameters: dict
    mode: Literal["sync", "human"] = "sync"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FrontendToolSpec.name must be non-empty")
        if self.mode not in _MODES:
            raise ValueError(
                f"FrontendToolSpec.mode must be one of {_MODES}, got {self.mode!r}"
            )
        if not isinstance(self.parameters, dict):
            raise TypeError("FrontendToolSpec.parameters must be a JSON Schema dict")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of a surface, as decided by :meth:`Surface.authorize`.

    ``id`` must be stable per person: it is part of the Hermes session id. ``claims`` carries whatever the host wants
    available to :meth:`Surface.build_instructions` (roles, tenant, ...). Not hashable when ``claims`` is a dict.
    """

    id: str
    display: str = ""
    claims: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class TurnContext:
    """Everything a surface may use to build per-turn instructions.

    ``run_input`` is the AG-UI ``RunAgentInput`` exactly as received from the client (camelCase keys; the values of
    client ``context[]`` entries are JSON *strings*, decode them yourself).
    """

    surface_id: str
    principal: Principal
    thread_id: str
    run_id: str
    session_id: str
    run_input: dict


class Surface(ABC):
    """One agentic UI surface. Subclass and set the class attributes, implement the two abstract methods.

    Attributes:
        id: CopilotKit ``agentId`` (route segment ``agent/{id}/...``); unique within a registry.
        profile: name of the Hermes profile that serves this surface (its toolsets must include ``agui_frontend``
            when ``frontend_tools`` is non-empty).
        title: human label (used as the agent name/description in the runtime ``info`` response).
        frontend_tools: the browser tools this surface offers; their schemas are the authoritative ones.
    """

    id: str
    profile: str
    title: str
    frontend_tools: Sequence[FrontendToolSpec] = ()

    @abstractmethod
    def authorize(self, request) -> Principal | None:
        """Authenticate/authorize the incoming HTTP request (a Starlette/FastAPI ``Request``).

        Return the :class:`Principal`, or ``None`` to deny (the route answers 403 and the surface is hidden from
        ``info``). Must not raise for an ordinary unauthenticated request.
        """

    def session_id(self, principal: Principal, thread_id: str) -> str:
        """Hermes ``session_id`` for this principal's thread; prefixed per surface so sessions never collide."""
        return f"agui:{self.id}:{principal.id}:{thread_id}"

    @abstractmethod
    def build_instructions(self, ctx: TurnContext) -> str:
        """Per-turn instructions sent with the Hermes run (``instructions``); Hermes never stores them."""


class SurfaceRegistry:
    """Surfaces by id. Registration happens at host start-up; lookups happen per request."""

    def __init__(self) -> None:
        self._surfaces: dict[str, Surface] = {}

    def register(self, s: Surface) -> None:
        """Add a surface. Raises ``ValueError`` on a duplicate/empty id or duplicate frontend tool names."""
        sid = getattr(s, "id", "")
        if not sid:
            raise ValueError("surface id must be non-empty")
        if sid in self._surfaces:
            raise ValueError(f"surface {sid!r} already registered")
        names = [t.name for t in s.frontend_tools]
        if len(names) != len(set(names)):
            raise ValueError(
                f"surface {sid!r} has duplicate frontend tool names: {names}"
            )
        self._surfaces[sid] = s

    def get(self, surface_id: str) -> Surface:
        """The surface with this id; raises ``KeyError`` if unknown."""
        try:
            return self._surfaces[surface_id]
        except KeyError:
            raise KeyError(f"unknown surface {surface_id!r}") from None

    def list_for(self, request) -> list[Surface]:
        """Surfaces whose :meth:`Surface.authorize` accepts ``request``, in registration order."""
        return [s for s in self._surfaces.values() if s.authorize(request) is not None]
