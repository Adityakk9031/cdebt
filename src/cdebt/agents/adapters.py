"""Launch the developer's own coding agent headlessly with the cdebt MCP server attached.

Verified against a real CLI: claude_code (Claude Code 2.1.299, flags read from `claude --help`).
UNVERIFIED (CLI not installed here, flags from documentation/memory): codex, cursor.
`command` is the generic escape hatch; `builtin` means "no agent" (type-hint generator)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cdebt.agents.userconfig import AgentChoice

AGENT_NAMES = ("claude_code", "codex", "cursor", "command", "builtin")
CLAUDE_MAC_GLOB = (
    "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
)
TOOL_PREFIX = "mcp__cdebt__"
PROMPT = """You are helping `cdebt` write a comprehension quiz about code in this repository.
Use the cdebt MCP tools only: call get_targets, read each target's source and nearby tests
(read-only tools are allowed), then call propose_probe up to {n} times per target. Mix kinds:
return_value (call_inputs: {{"args": [...], "kwargs": {{...}}}}), branch_taken and
variable_at_line (need `line`; variable_at_line also `variable`), constant_value (`name`),
what_calls, which_test_fails. Choose inputs that exercise different branches and edge cases;
prefer questions whose answer a reader must reason about, not guess. For code that touches time,
randomness, files or the network, you may supply harness_code defining `def probe():` that mocks
that I/O and calls the REAL target (target_module.<name>); never replace the target itself.
cdebt runs every proposal and works out the answers itself; you will only be told accepted or
rejected with a reason. Do not try to find or compute answers, and do not edit anything.
When done, reply with one short line."""


@dataclass
class AgentRun:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path = Path(".")
    notes: list[str] = field(default_factory=list)


def mcp_argv(session_dir: Path) -> list[str]:
    """How an agent launches the cdebt MCP server (stdio)."""
    code = "from cdebt.cli import app; app()"
    return [sys.executable, "-c", code, "mcp", "--session", str(session_dir)]


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    hits = sorted(Path.home().glob(CLAUDE_MAC_GLOB))
    return str(hits[-1]) if hits else None


def detect() -> dict[str, str]:
    """Agent CLIs found on this machine: name -> path."""
    out: dict[str, str] = {}
    c = find_claude()
    if c:
        out["claude_code"] = c
    for name, exe in (("codex", "codex"), ("cursor", "cursor-agent")):
        p = shutil.which(exe)
        if p:
            out[name] = p
    return out


def _mcp_json(session_dir: Path) -> str:
    argv = mcp_argv(session_dir)
    return json.dumps({"mcpServers": {"cdebt": {"command": argv[0], "args": argv[1:]}}})


def build(choice: AgentChoice, session_dir: Path, repo: Path, per_target: int) -> AgentRun:
    prompt = PROMPT.format(n=per_target)
    name = choice.name
    if name == "claude_code":
        exe = find_claude()
        if not exe:
            raise FileNotFoundError("claude CLI not found")
        cfg = session_dir / "mcp.json"
        cfg.write_text(_mcp_json(session_dir))
        tools = [TOOL_PREFIX + "get_targets", TOOL_PREFIX + "propose_probe"]
        argv = [
            exe, "-p", prompt,
            "--mcp-config", str(cfg), "--strict-mcp-config",
            "--tools", "Read,Grep,Glob",  # read-only built-ins: no Bash, Edit or Write
            "--allowedTools", ",".join(tools),
            "--permission-mode", "dontAsk",  # anything not allowed is denied, never prompted
            "--no-session-persistence", "--max-budget-usd", "2",
        ]  # fmt: skip
        return AgentRun(argv, cwd=repo)
    if name == "codex":  # UNVERIFIED: `codex` is not installed here; flags from its docs
        exe = shutil.which("codex")
        if not exe:
            raise FileNotFoundError("codex CLI not found")
        m = mcp_argv(session_dir)
        argv = [
            exe, "exec", "--sandbox", "read-only",
            "-c", f"mcp_servers.cdebt.command={json.dumps(m[0])}",
            "-c", f"mcp_servers.cdebt.args={json.dumps(m[1:])}",
            prompt,
        ]  # fmt: skip
        return AgentRun(argv, cwd=repo, notes=["codex adapter is UNVERIFIED against a real CLI"])
    if name == "cursor":  # UNVERIFIED: no cursor-agent here; needs the MCP server registered
        exe = shutil.which("cursor-agent")
        if not exe:
            raise FileNotFoundError("cursor-agent CLI not found")
        return AgentRun(
            [exe, "-p", prompt, "--output-format", "text"],
            env={"CDEBT_MCP_CMD": shlex.join(mcp_argv(session_dir))},
            cwd=repo,
            notes=[
                "cursor adapter is UNVERIFIED; register `cdebt mcp` in Cursor's own MCP config "
                "(see `cdebt agent status`)"
            ],
        )
    if name == "command":
        if not choice.command:
            raise ValueError("the `command` agent needs a command")
        env = {"CDEBT_MCP_CMD": shlex.join(mcp_argv(session_dir)), "CDEBT_PROMPT": prompt}
        return AgentRun(shlex.split(choice.command), env=env, cwd=repo)
    raise ValueError(f"unknown agent {name!r}; one of {AGENT_NAMES}")


def child_env(extra: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(extra)
    return env
