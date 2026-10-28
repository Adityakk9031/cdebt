"""`cdebt mcp`: the MCP server an agent talks to. It exposes exactly two tools.

- get_targets(): read-only list of the not-yet-understood units (path, symbol, source, tests).
- propose_probe(...): submit a question. The proposal is executed in the sandbox; the reply is
  only "accepted" or "rejected: <reason>". The correct answer is NEVER returned.
"""

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from cdebt.agents.probes import KINDS, Proposal, Rejected, execute, parse_proposal
from cdebt.agents.session import Session
from cdebt.config import Config, load_config


def build_server(session_dir: Path) -> MCPServer:
    sess = Session.load(session_dir)
    repo = Path(sess.data.repo)
    cfg: Config = load_config(repo)
    server = MCPServer(
        "cdebt",
        instructions=(
            "Propose comprehension-quiz questions about the targets. You are told only whether "
            "each is accepted; cdebt computes the answers by running the code."
        ),
    )

    @server.tool()
    def get_targets() -> list[dict[str, Any]]:
        """AI-written, not-yet-understood functions: id, path, symbol, signature, source, tests."""
        return [t.model_dump() for t in sess.data.targets]

    @server.tool()
    def propose_probe(
        target: str,
        kind: str,
        question: str = "",
        call_inputs: dict[str, Any] | None = None,
        harness_code: str | None = None,
        line: int | None = None,
        variable: str | None = None,
        name: str | None = None,
        world: dict[str, Any] | None = None,
    ) -> str:
        """Submit one question about `target` (an id from get_targets). kind is one of
        return_value, branch_taken, variable_at_line, which_test_fails, what_calls,
        constant_value, net_requests, commands_run, files_written, stdout_text. `world` is the
        fake world to run in: {env, files, http, commands, now}. A proposal with harness_code
        must also give `question` (the scenario). Returns 'accepted' or 'rejected: <reason>'
        (never the answer)."""
        t = sess.target(target)
        if t is None:
            return "rejected: unknown target id (use an id from get_targets)"
        if sess.submitted(target) >= sess.data.max_per_target:
            return f"rejected: budget of {sess.data.max_per_target} proposals for this target used"
        if kind not in KINDS:
            return f"rejected: kind must be one of {', '.join(KINDS)}"
        raw: dict[str, Any] = {
            "target": t.symbol,
            "kind": kind,
            "question": question,
            "call_inputs": call_inputs or {},
            "harness_code": harness_code,
            "line": line,
            "variable": variable,
            "name": name,
            "world": world,
        }
        try:
            prop: Proposal = parse_proposal(raw)
            execute(repo, t.path, prop, cfg, proposer=f"agent:{sess.data.agent}")
        except Rejected as e:
            sess.record(target, raw, False, str(e))
            return f"rejected: {e}"
        except Exception as e:  # never let a proposal crash the server or leak a traceback
            sess.record(target, raw, False, type(e).__name__)
            return f"rejected: internal error ({type(e).__name__})"
        sess.record(target, raw, True, "")
        return "accepted"

    return server


def serve(session_dir: Path) -> None:
    build_server(session_dir).run(transport="stdio")
