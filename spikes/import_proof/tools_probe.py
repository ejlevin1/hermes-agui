"""Print the tool names Hermes offers for the HERMES_HOME in the environment (run with the managed env's python).
Usage: HERMES_HOME=<home> <python> tools_probe.py"""

import json

from hermes_cli.plugins import discover_plugins
from model_tools import get_tool_definitions

discover_plugins()
names = sorted(
    t["function"]["name"]
    for t in get_tool_definitions(quiet_mode=True, skip_tool_search_assembly=True)
)
print(
    json.dumps(
        {
            "call_frontend_tool_offered": "call_frontend_tool" in names,
            "n_tools": len(names),
        }
    )
)
