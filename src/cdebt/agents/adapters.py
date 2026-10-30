"""Launch the developer's own coding agent headlessly with the cdebt MCP server attached.

Verified against a real CLI: claude_code (Claude Code 2.1.299, flags read from `claude --help`).
UNVERIFIED (CLI not installed here, flags from documentation/memory): codex, cursor.
`command` is the generic escape hatch; `builtin` means "no agent" (type-hint generator)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
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
(read-only tools are allowed), then call propose_probe up to {n} times per target. Any question
is welcome as long as cdebt can answer it by running the real code. Kinds: return_value
(call_inputs: {{"args": [...], "kwargs": {{...}}}}), branch_taken and variable_at_line (need
`line`; variable_at_line also `variable`), constant_value (`name`), what_calls,
which_test_fails, and the effect kinds net_requests, commands_run, files_written and
stdout_text (which requests / commands / file changes / printed text the call produces).
Choose inputs that exercise different branches and edge cases; prefer questions whose answer a
reader must reason about, not guess. Use realistic inputs the code was written for, and avoid
tricks that test the programming language rather than this code.
Do not avoid code that touches the environment, files, the network, shell commands, the clock
or randomness: cdebt runs it in a FAKE WORLD and you describe that world in `world`:
{{"env": {{"NAME": "value"}}, "files": {{"path/in/repo": "file text"}},
"http": [{{"url": "substring or *", "method": "GET", "status": 200, "body": {{...}}}}],
"commands": [{{"cmd": "substring of the command line", "returncode": 0, "stdout": "..."}}],
"now": "2024-03-01T12:00:00Z"}}. Nothing real is contacted or run: requests (urllib, http.client,
requests, httpx), shell commands, the clock, random numbers, UUIDs and the environment are all
faked, files are written to a scratch copy, async functions run. Unlisted requests get 200 and
{{}}, unlisted commands exit 0 with no output. Create scenarios that make the code take its
interesting paths: success, failure statuses, missing files, a command that fails. For anything
else you may supply harness_code defining `def probe():` that calls the REAL target
(target_module.<name>) and returns what the question asks; never replace the target itself. A
harness_code proposal MUST also include `question`: one sentence describing the scenario.
Cover EVERY target: first propose one plain return_value (or stdout_text / effect) question
for each target, then add more. When a proposal is rejected, read the reason and send a fixed
one: a harness must call the real target (never a mock or a copy of it); `line` must be a code
line inside the target's own body; for a method, build the object in harness_code and call the
method on it; call_inputs must match the function's signature; for a value that is a big
object (or one whose repr shows a memory address), return one field of it instead. Writes
outside the repository fail as on a read-only disk, and files outside it do not exist.
cdebt runs every proposal and works out the answers itself; you will only be told accepted or
rejected with a reason. Do not try to find or compute answers, and do not edit anything.
When done, reply with one short line."""


@dataclass
class AgentRun:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path = Path(".")
    notes: list[str] = field(default_factory=list)


def _trusted() -> bool:
    """Whether the person running cdebt chose the host sandbox (`--trusted` / CDEBT_TRUSTED=1).
    The MCP server is a separate process, so the choice is passed on explicitly; it is never
    taken from anything in the repository."""
    from cdebt import sandbox

    return sandbox.DEFAULT_ISOLATION == "host"


def mcp_argv(session_dir: Path) -> list[str]:
    """How an agent launches the cdebt MCP server (stdio)."""
    from cdebt import sandbox

    code = "from cdebt.cli import app; app()"
    pp = os.environ.get(sandbox.PROJECT_PYTHON_ENV)  # the person's choice, passed on explicitly
    return [
        sys.executable, "-c", code,
        *(["--project-python", pp] if pp else []),
        "mcp",
        *(["--trusted"] if _trusted() else []),
        "--session", str(session_dir),
    ]  # fmt: skip


def _runs(exe: str) -> bool:
    """True when `exe --version` works on this machine (the Claude desktop app ships a Linux
    binary for its own VM that matches the glob below but cannot run here)."""
    try:
        r = subprocess.run(  # noqa: S603 - a fixed flag on a found CLI
            [exe, "--version"], capture_output=True, text=True, timeout=20,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found and _runs(found):
        return found
    for hit in sorted(Path.home().glob(CLAUDE_MAC_GLOB), reverse=True):
        if _runs(str(hit)):
            return str(hit)
    return None


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
