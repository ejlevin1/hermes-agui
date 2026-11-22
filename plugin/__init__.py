"""Hermes plugin: registers the native tool ``call_frontend_tool`` in toolset ``agui_frontend``."""

from __future__ import annotations

try:
    from .tool import SCHEMA, TOKEN_ENV, make_handler
except ImportError:  # loaders that import the plugin dir without package context
    import importlib.util
    import pathlib
    import sys

    _p = pathlib.Path(__file__).with_name("tool.py")
    _spec = importlib.util.spec_from_file_location("agui_frontend_tool", _p)
    _m = importlib.util.module_from_spec(_spec)
    sys.modules["agui_frontend_tool"] = _m
    _spec.loader.exec_module(_m)
    SCHEMA, TOKEN_ENV, make_handler = _m.SCHEMA, _m.TOKEN_ENV, _m.make_handler


def register(ctx) -> None:
    ctx.register_tool(
        name="call_frontend_tool",
        toolset="agui_frontend",
        schema=SCHEMA,
        handler=make_handler(ctx.get_config),
        requires_env=[TOKEN_ENV],
        description="Forward a frontend tool call to the user's browser (AG-UI broker)",
    )
