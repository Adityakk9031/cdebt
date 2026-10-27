"""Turn an agent's PROPOSAL into a verified probe. The agent proposes; the sandbox decides.

Rules (docs/METHOD.md, "Agent-proposed questions"):
- every proposal is executed in the sandbox and the true answer comes from that execution
  (an execution trace, the SCIP call graph, a mutation run or a module import);
- anything that cannot be executed, differs between two runs, or whose trace shows the REAL
  target's own lines did not run (a harness that tests a mock) is rejected with a reason;
- the agent's text never decides correctness and is never graded.
"""

from __future__ import annotations

import ast
import hashlib
import random
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from cdebt import sandbox
from cdebt.config import Config
from cdebt.models import CodeUnit, Probe
from cdebt.probes import callgraph, constants, mutation
from cdebt.probes.base import copy_tree, probe_id, shuffled, temp_root
from cdebt.probes.runtime import distractors, module_info
from cdebt.sandbox import (
    fake_world_uses,
    run_in_sandbox,
    screen_function,
    screen_module_level,
    world_note,
)
from cdebt.scip import index_is_fresh, load_all
from cdebt.units import extract_units

KINDS = (
    "return_value",
    "branch_taken",
    "variable_at_line",
    "which_test_fails",
    "what_calls",
    "constant_value",
)
MAX_HARNESS = 8000
MAX_TEXT = 300
BRANCHY = (ast.If, ast.For, ast.While, ast.Try, ast.BoolOp, ast.IfExp, ast.ExceptHandler)


class Proposal(BaseModel):
    """What an agent may send. There is deliberately no field for an answer."""

    target: str = Field(description="unit symbol, e.g. 'shipping' or 'Cart.total'")
    kind: Literal[
        "return_value",
        "branch_taken",
        "variable_at_line",
        "which_test_fails",
        "what_calls",
        "constant_value",
    ]
    question: str = ""
    call_inputs: dict[str, Any] = Field(default_factory=dict)  # {"args": [...], "kwargs": {...}}
    harness_code: str | None = None  # Python defining `def probe():`; may mock I/O, not the target
    line: int | None = None  # 1-indexed file line (branch_taken, variable_at_line)
    variable: str | None = None  # variable_at_line
    name: str | None = None  # constant_value


class Rejected(Exception):
    """A proposal that cannot be verified by execution. The message is safe to show the agent."""


def parse_proposal(data: dict[str, Any]) -> Proposal:
    try:
        return Proposal.model_validate(data)
    except ValidationError as e:
        raise Rejected("malformed proposal: " + "; ".join(x["msg"] for x in e.errors()[:3])) from e


def branch_count(source: str, unit: CodeUnit) -> int:
    lo, hi = min(a for a, _ in unit.ranges), max(b for _, b in unit.ranges)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, BRANCHY) and lo <= getattr(node, "lineno", 0) <= hi:
            n += 1
    return n


def difficulty_of(kind: str, branches: int = 0) -> Literal["easy", "medium", "hard"]:
    """easy: a module constant, or a value read off one call of a simple function.
    medium: a call of a function with several branches, or "did this line run?".
    hard: what a variable held mid-run (a trace), who calls it (call graph), or which test fails."""
    if kind in ("constant", "constant_value"):
        return "easy"
    if kind in ("runtime", "return_value"):
        return "medium" if branches >= 3 else "easy"
    if kind == "branch_taken":
        return "medium"
    return "hard"


def _call_text(symbol: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
    return f"{symbol}({', '.join(parts)})"


def _options(value_repr: str, rng: random.Random) -> list[str] | None:
    try:
        v = ast.literal_eval(value_repr)
    except (ValueError, SyntaxError):
        return None
    opts = shuffled([value_repr, *distractors(v, rng)[:3]], rng)
    return opts if len(opts) >= 2 else None


def _unit(units: list[CodeUnit], symbol: str) -> CodeUnit:
    for u in units:
        if u.symbol == symbol and not u.symbol.startswith("<"):
            return u
    raise Rejected(f"no function or method named {symbol!r} in this file")


def _finish(p: Probe, proposer: str, branches: int, harness: str | None, text: str) -> Probe:
    p.proposer = proposer
    p.harness_hash = hashlib.sha256(harness.encode()).hexdigest() if harness else None
    p.difficulty = difficulty_of(p.kind, branches)
    if text:
        p.evidence["agent_text"] = text[:MAX_TEXT]  # audit only; never used for grading
    p.evidence["proposed_by"] = proposer
    return p


def execute(
    repo: Path,
    rel: str,
    prop: Proposal,
    cfg: Config,
    *,
    proposer: str,
    seed: int = 0,
    source: str | None = None,
) -> Probe:
    """Verify one proposal by execution and return the Probe (with its true answer) or raise
    Rejected. The returned Probe holds the answer: it is for cdebt, never for the agent. The
    proposal itself is stored in `evidence["spec"]` so CI can re-execute it (cdebt.recheck)."""
    p = _execute(repo, rel, prop, cfg, proposer=proposer, seed=seed, source=source)
    p.evidence["spec"] = prop.model_dump(exclude_none=True)
    return p


def _execute(
    repo: Path,
    rel: str,
    prop: Proposal,
    cfg: Config,
    *,
    proposer: str,
    seed: int = 0,
    source: str | None = None,
) -> Probe:
    if not rel.endswith(".py"):
        raise Rejected("agent-proposed probes support Python files only (TypeScript: UNVERIFIED)")
    source = source if source is not None else (repo / rel).read_text()
    units = extract_units(rel, source)
    rng = random.Random(seed)
    text = prop.question.strip()[:MAX_TEXT]

    if prop.kind == "constant_value":
        if not prop.name:
            raise Rejected("constant_value needs `name`")
        ps, _ = constants.generate(repo, rel, source, units, seed, per_unit=50)
        for p in ps:
            if p.evidence.get("name") == prop.name:
                return _finish(p, proposer, 0, None, text)
        raise Rejected(f"{prop.name!r} is not a verifiable module-level constant here")
    unit = _unit(units, prop.target)
    branches = branch_count(source, unit)
    if prop.kind == "what_calls":
        if not index_is_fresh(repo, cfg.extensions):
            raise Rejected("no fresh SCIP index (run `cdebt index`); call-graph questions need it")
        ps, _ = callgraph.generate(repo, rel, units, load_all(repo), seed, per_unit=3)
        for p in ps:
            if p.symbol == prop.target:
                return _finish(p, proposer, branches, None, text)
        raise Rejected("no call-graph question can be answered for that target")
    if prop.kind == "which_test_fails":
        if not sandbox.is_trusted():
            raise Rejected("which_test_fails runs the project's tests and needs --trusted")
        ps, _ = mutation.generate(repo, rel, source, units, seed)
        for p in ps:
            if p.symbol == prop.target:
                return _finish(p, proposer, branches, None, text)
        raise Rejected("no test-based question is available (needs a green baseline suite)")
    return _trace_probe(repo, rel, prop, source, unit, branches, rng, proposer, text, seed)


def _trace_probe(
    repo: Path,
    rel: str,
    prop: Proposal,
    source: str,
    unit: CodeUnit,
    branches: int,
    rng: random.Random,
    proposer: str,
    text: str,
    seed: int,
) -> Probe:
    why = screen_module_level(source)
    if why:
        raise Rejected("module has import-time side effects: " + "; ".join(why[:2]))
    leaf = unit.symbol.split(".")[-1]
    harness = prop.harness_code
    if harness is not None:
        if len(harness) > MAX_HARNESS:
            raise Rejected("harness too long")
        try:
            tree = ast.parse(harness)
        except SyntaxError as e:
            raise Rejected(f"harness does not parse: {e.msg}") from e
        if not any(
            isinstance(n, ast.FunctionDef) and n.name == "probe" and not n.args.args
            for n in tree.body
        ):
            raise Rejected("harness must define a top-level `def probe():` with no arguments")
        if not text:
            raise Rejected("a harness probe needs `question` text describing the scenario")
    else:
        fn_why = screen_function(source, leaf, fake_world=True)
        if fn_why:
            raise Rejected("not safely executable without a harness: " + "; ".join(fn_why[:2]))
    args = list(prop.call_inputs.get("args", []))
    kwargs = dict(prop.call_inputs.get("kwargs", {}))
    lo = min(a for a, _ in unit.ranges)
    hi = max(b for _, b in unit.ranges)
    src_lines = source.splitlines()
    if prop.kind in ("branch_taken", "variable_at_line"):
        if prop.line is None or not (lo < prop.line <= hi):
            raise Rejected(f"`line` must be inside the body of {unit.symbol} ({lo + 1}-{hi})")
        if not src_lines[prop.line - 1].strip() or src_lines[prop.line - 1].strip()[0] == "#":
            raise Rejected("`line` must be a line of code")
    if prop.kind == "variable_at_line" and not prop.variable:
        raise Rejected("variable_at_line needs `variable`")

    mod, roots = module_info(repo, rel)
    root = temp_root("cdebt-agent-")
    try:
        copy_tree(repo, root)
        (root / rel).write_text(source)  # exactly the text that is hashed
        res = run_in_sandbox(
            {
                "mode": "trace",
                "module": mod,
                "qual": unit.symbol,
                "func": leaf,
                "first": lo,
                "last": hi,
                "tfile": str(root / rel),
                "sys_path": [str(root / r) for r in roots],
                "harness": harness,
                "args_repr": repr(args),
                "kwargs_repr": repr(kwargs),
                "watch_line": prop.line if prop.kind == "variable_at_line" else None,
                "watch_var": prop.variable,
                "fake_world": True,
            },
            root=root,
            timeout=15,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    pl: dict[str, Any] = res.payload or {}
    if res.timed_out:
        raise Rejected("timed out in the sandbox")
    if res.violations:
        raise Rejected(f"blocked by the sandbox: {res.violations[:2]}")
    if "error" in pl or "results" not in pl:
        raise Rejected(f"could not execute: {str(pl.get('error', 'no result'))[:200]}")
    if not pl.get("same_target", False):
        raise Rejected("the harness replaced the target; only I/O may be mocked, never the target")
    calls = pl["real_calls"]
    executed = [{n for n in run if lo <= n <= hi} for run in pl["lines"]]
    if min(calls) < 1 or not executed[0]:
        raise Rejected(
            "the real target's own lines did not execute (the harness tested a mock or never "
            "called the target)"
        )
    if pl["results"][0] != pl["results"][1] or executed[0] != executed[1]:
        raise Rejected("non-deterministic: two runs differed (mock time, randomness and I/O)")
    call = _call_text(unit.symbol, args, kwargs)
    uses = fake_world_uses(source, leaf)
    note = "" if harness else world_note(uses)
    ev: dict[str, object] = {
        "isolation": res.isolation,
        "deterministic_runs": 2,
        "proposed_kind": prop.kind,
        "executed_lines": sorted(executed[0]),
        "fake_world": uses,
    }

    def build(kind: str, q: str, opts: list[str], answer: str) -> Probe:
        p = Probe(
            id=probe_id(kind, rel, unit.symbol, q, seed),
            kind=kind,
            path=rel,
            symbol=unit.symbol,
            question=q,
            options=opts,
            answer=answer,
            verified_by="execution-trace",
            seed=seed,
            evidence=ev,
        )
        return _finish(p, proposer, branches, harness, text)

    if prop.kind == "return_value":
        kind_, value = pl["results"][0]
        if kind_ == "ok":
            opts = _options(value, rng)
            if opts is None:
                raise Rejected("return value is not a simple literal; cannot build answer options")
            answer = value
        else:
            answer = f"raises {value}"
            opts = shuffled(
                [answer, "returns None", *[f"raises {e}" for e in ("ValueError", "KeyError")]], rng
            )[:4]
        ev["result"] = answer
        q = text if harness else f"What does `{call}` evaluate to?{note}"
        return build("return_value", q, opts, answer)
    if prop.kind == "branch_taken":
        assert prop.line is not None
        ran = prop.line in executed[0]
        shown = src_lines[prop.line - 1].strip()
        ev["line"] = prop.line
        q = (
            f"{text} " if harness and text else f"When `{call}` runs{note}, "
        ) + f"does line {prop.line} of {rel} (`{shown}`) execute?"
        return build("branch_taken", q, ["yes", "no"], "yes" if ran else "no")
    assert prop.line is not None and prop.variable is not None
    seen = pl["watch"][0]
    if not seen or seen != pl["watch"][1] or seen[0] == "<unbound>":
        raise Rejected(
            f"`{prop.variable}` was not bound (or not stable) before line {prop.line} ran"
        )
    opts = _options(seen[0], rng)
    if opts is None:
        raise Rejected("that variable's value is not a simple literal")
    shown = src_lines[prop.line - 1].strip()
    ev.update({"line": prop.line, "variable": prop.variable})
    q = (
        f"{text} " if harness and text else f"When `{call}` runs{note}, "
    ) + f"what is `{prop.variable}` just before line {prop.line} (`{shown}`) runs?"
    return build("variable_at_line", q, opts, seen[0])
