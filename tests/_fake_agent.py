"""A FAKE coding agent for tests: speaks real MCP (stdio) to `cdebt mcp` and proposes good probes,
bad probes (mock-only, non-deterministic, unsafe, unknown target) and one bad kind.
It also acts like an agent with file-read access: it scans the session directory for answers and
reports any it finds. Output: a JSON file ($FAKE_AGENT_REPORT) {"replies": [...], "leaked": bool}."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

MOCK = "def probe():\n    target_module.shipping = lambda *a, **k: 3\n    return target_module.shipping(1)\n"
CLOCK = (
    "def probe():\n    import time\n    time.time = lambda: 1000\n"
    "    return target_module.stamp()\n"
)
NOT_TARGET = "def probe():\n    return 3\n"
FLAKY = "def probe():\n    import time\n    return time.time_ns() + target_module.stamp()\n"


async def main() -> None:
    argv = shlex.split(os.environ["CDEBT_MCP_CMD"])
    session_dir = Path(argv[-1])
    params = StdioServerParameters(command=argv[0], args=argv[1:])
    replies: list[str] = []
    async with Client(params) as c:
        tl = await c.call_tool("get_targets", {})
        sc: Any = tl.structured_content
        targets = sc["result"] if sc else json.loads(tl.content[0].text)  # type: ignore[union-attr]
        ids = {t["symbol"]: t["id"] for t in targets}
        sid, stamp = ids["shipping"], ids["stamp"]

        async def prop(**kw: Any) -> None:
            r = await c.call_tool("propose_probe", kw)
            replies.append(r.content[0].text)  # type: ignore[union-attr]

        await prop(
            target=sid, kind="return_value", call_inputs={"args": [60], "kwargs": {"express": True}}
        )
        await prop(target=sid, kind="branch_taken", call_inputs={"args": [10]}, line=8)
        await prop(
            target=sid,
            kind="variable_at_line",
            call_inputs={"args": [10, True]},
            line=9,
            variable="base",
        )
        await prop(
            target=stamp, kind="return_value", question="stamp, fake clock", harness_code=CLOCK
        )
        # bad ones
        await prop(target=sid, kind="return_value", question="mock", harness_code=MOCK)
        await prop(
            target=sid, kind="return_value", question="not the target", harness_code=NOT_TARGET
        )
        await prop(target=stamp, kind="return_value")  # uses time: unsafe without a harness
        await prop(target=stamp, kind="return_value", question="flaky", harness_code=FLAKY)
        await prop(target="nope::nope", kind="return_value")
        await prop(target=sid, kind="not_a_kind")
    leaked = False
    for f in session_dir.rglob("*"):
        if f.is_file() and f.name != "mcp.json":
            txt = f.read_text(errors="replace")
            if '"answer"' in txt or "execution-trace" in txt:
                leaked = True
    Path(os.environ["FAKE_AGENT_REPORT"]).write_text(
        json.dumps({"replies": replies, "leaked": leaked})
    )


asyncio.run(main())
