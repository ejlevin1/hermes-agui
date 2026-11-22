"""Stand-in for the gateway plugin `plugin/` of hermes-agui: one tool in toolset agui_frontend."""

import json


def _handler(args, **kwargs):
    return json.dumps({"status": "stub"})


def register(ctx):
    ctx.register_tool(
        name="call_frontend_tool",
        toolset="agui_frontend",
        schema={
            "name": "call_frontend_tool",
            "description": "stub",
            "parameters": {
                "type": "object",
                "properties": {"tool_name": {"type": "string"}},
            },
        },
        handler=_handler,
    )
