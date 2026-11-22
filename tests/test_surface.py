import pytest

import hermes_agui
from hermes_agui import (
    FrontendToolSpec,
    Principal,
    Surface,
    SurfaceRegistry,
    TurnContext,
)


class FakeRequest:
    def __init__(self, user: str | None):
        self.user = user


class DemoSurface(Surface):
    profile = "demo-profile"
    title = "Demo"

    def __init__(self, sid: str, allowed: set[str], tools=()):
        self.id = sid
        self.allowed = allowed
        self.frontend_tools = tools

    def authorize(self, request):
        if request.user in self.allowed:
            return Principal(id=request.user, display=request.user.title())
        return None

    def build_instructions(self, ctx: TurnContext) -> str:
        return f"surface={ctx.surface_id} user={ctx.principal.id}"


TOOL = FrontendToolSpec(
    name="set_field", description="Set a field", parameters={"type": "object"}
)


def test_register_and_get():
    reg = SurfaceRegistry()
    a = DemoSurface("a", {"u1"})
    reg.register(a)
    assert reg.get("a") is a
    with pytest.raises(KeyError):
        reg.get("missing")


def test_register_rejects_duplicate_and_empty_id():
    reg = SurfaceRegistry()
    reg.register(DemoSurface("a", set()))
    with pytest.raises(ValueError):
        reg.register(DemoSurface("a", set()))
    with pytest.raises(ValueError):
        reg.register(DemoSurface("", set()))


def test_register_rejects_duplicate_tool_names():
    with pytest.raises(ValueError):
        SurfaceRegistry().register(DemoSurface("a", set(), tools=(TOOL, TOOL)))


def test_default_session_id_has_surface_prefix():
    s = DemoSurface("form.new", {"u1"})
    p = s.authorize(FakeRequest("u1"))
    assert s.session_id(p, "t-1") == "agui:form.new:u1:t-1"
    other = DemoSurface("form.step", {"u1"})
    assert other.session_id(p, "t-1") != s.session_id(p, "t-1")


def test_list_for_filters_by_authorize_in_order():
    reg = SurfaceRegistry()
    a, b, c = (
        DemoSurface("a", {"u1", "u2"}),
        DemoSurface("b", {"u2"}),
        DemoSurface("c", {"u1"}),
    )
    for s in (a, b, c):
        reg.register(s)
    assert reg.list_for(FakeRequest("u1")) == [a, c]
    assert reg.list_for(FakeRequest("u2")) == [a, b]
    assert reg.list_for(FakeRequest(None)) == []


def test_build_instructions_gets_turn_context():
    s = DemoSurface("a", {"u1"})
    p = s.authorize(FakeRequest("u1"))
    ctx = TurnContext(
        "a", p, "t", "r", s.session_id(p, "t"), {"threadId": "t", "messages": []}
    )
    assert s.build_instructions(ctx) == "surface=a user=u1"


def test_surface_is_abstract():
    with pytest.raises(TypeError):
        Surface()


def test_frontend_tool_spec_defaults_and_validation():
    assert TOOL.mode == "sync"
    assert FrontendToolSpec("x", "d", {}, mode="human").mode == "human"
    with pytest.raises(ValueError):
        FrontendToolSpec("x", "d", {}, mode="async")
    with pytest.raises(ValueError):
        FrontendToolSpec("", "d", {})
    with pytest.raises(TypeError):
        FrontendToolSpec("x", "d", "not-a-dict")


def test_principal_defaults():
    p = Principal(id="u1")
    assert p.display == "" and dict(p.claims) == {}
    assert Principal(id="u2").claims is not p.claims


def test_public_exports():
    assert hermes_agui.FRONTEND_TOOL == "call_frontend_tool"
    for name in hermes_agui.__all__:
        assert hasattr(hermes_agui, name)


class _FakeStore:
    def __init__(self):
        self.d = {}

    def append(self, thread_key, message):
        self.d.setdefault(thread_key, []).append(message)

    def messages(self, thread_key):
        return list(self.d.get(thread_key, []))


class _FakeBroker:
    def open_session(self, session_id, tools): ...
    async def wait_call(self, session_id, tool_call_id, timeout): ...
    def resolve(self, session_id, tool_call_id, content):
        return False

    def drop_session(self, session_id): ...


class _FakeHermes:
    async def start_run(self, *, profile, session_id, input_text, instructions):
        return "run-1"

    async def events(self, *, profile, run_id):
        yield {"event": "run.completed"}

    async def stop(self, *, profile, run_id): ...


def test_protocols_are_structural():
    assert isinstance(_FakeStore(), hermes_agui.TranscriptStore)
    assert isinstance(_FakeBroker(), hermes_agui.Broker)
    assert isinstance(_FakeHermes(), hermes_agui.HermesClient)
    assert not isinstance(object(), hermes_agui.Broker)
