"""Runtime probes: "what does f(args) return?" Answer = result of SANDBOXED EXECUTION."""

from __future__ import annotations

import ast
import json
import random
import shutil
from pathlib import Path

from cdebt.models import CodeUnit, Probe
from cdebt.probes.base import copy_tree, probe_id, shuffled, temp_root
from cdebt.sandbox import run_in_sandbox, screen_function, screen_module_level

WORDS = ["ab", "hello", "x", "", "Cat", "a b", "zed"]
EXC = ["ValueError", "TypeError", "KeyError", "ZeroDivisionError", "IndexError"]


class Unsupported(Exception):
    pass


def _ann_name(a: ast.expr) -> str:
    if isinstance(a, ast.Name):
        return a.id
    if isinstance(a, ast.Attribute):
        return a.attr
    raise Unsupported("annotation")


def gen_value(a: ast.expr | None, rng: random.Random) -> object:
    if a is None:
        raise Unsupported("missing type hint")
    if isinstance(a, ast.Constant) and a.value is None:
        return None
    if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):  # X | None
        opts = [a.left, a.right]
        non_none = [o for o in opts if not (isinstance(o, ast.Constant) and o.value is None)]
        if len(non_none) == 1:
            return None if rng.random() < 0.2 else gen_value(non_none[0], rng)
        raise Unsupported("union")
    if isinstance(a, ast.Subscript):
        base = _ann_name(a.value)
        sl = a.slice
        if base == "Optional":
            return None if rng.random() < 0.2 else gen_value(sl, rng)
        if base in {"list", "List", "set", "Set"}:
            vals = [gen_value(sl, rng) for _ in range(rng.randint(0, 4))]
            return set(vals) if base.lower() == "set" else vals
        if base in {"dict", "Dict"} and isinstance(sl, ast.Tuple) and len(sl.elts) == 2:
            return {
                gen_value(sl.elts[0], rng): gen_value(sl.elts[1], rng)
                for _ in range(rng.randint(0, 3))
            }
        if base in {"tuple", "Tuple"} and isinstance(sl, ast.Tuple):
            return tuple(gen_value(e, rng) for e in sl.elts if not isinstance(e, ast.Constant))
        raise Unsupported(f"generic {base}")
    n = _ann_name(a)
    if n == "bool":
        return rng.random() < 0.5
    if n == "int":
        return rng.randint(-5, 20)
    if n == "float":
        return rng.choice([0.5, 1.5, 2.0, -3.25, 10.0])
    if n == "str":
        return rng.choice(WORDS)
    raise Unsupported(f"type {n}")


def distractors(v: object, rng: random.Random) -> list[str]:
    c: list[object] = []
    if isinstance(v, bool):
        c = [not v]
    elif isinstance(v, int):
        c = [v + 1, v - 1, -v, v * 2, 0]
    elif isinstance(v, float):
        c = [v + 1.0, v - 1.0, -v, v * 2]
    elif isinstance(v, str):
        c = [v[::-1], v.upper(), v + "x", v[:-1], v.lower(), v.strip() + v.strip()]
    elif isinstance(v, (list, tuple)):
        t = type(v)
        c = [t(list(v)[::-1]), t(list(v)[:-1]), t(list(v) + list(v)[:1]), t(sorted(v, key=repr))]
    elif isinstance(v, set):
        c = [set(list(v)[:-1]), v | {0}]
    elif isinstance(v, dict):
        c = [dict(list(v.items())[:-1]), {k: str(x) for k, x in v.items()}, {}]
    elif v is None:
        c = [0, "", [], False]
    out = [repr(x) for x in c if x != v and repr(x) != repr(v)]
    return list(dict.fromkeys(out))


def module_info(repo: Path, rel: str) -> tuple[str, list[str]]:
    """(dotted module name, sys.path roots relative to repo) via __init__.py climbing."""
    p = Path(rel)
    parts = [p.stem]
    d = p.parent
    while (repo / d / "__init__.py").exists() and str(d) != ".":
        parts.insert(0, d.name)
        d = d.parent
    roots = [str(d), "src", "."]
    return ".".join(parts), list(dict.fromkeys(roots))


def _functions(source: str) -> list[ast.FunctionDef]:
    tree = ast.parse(source)
    return [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.decorator_list]


def generate(
    repo: Path, rel: str, source: str, units: list[CodeUnit], seed: int, per_unit: int = 2
) -> tuple[list[Probe], list[str]]:
    """Returns (probes, skip reasons). Every answer is computed by sandboxed execution."""
    skips: list[str] = []
    why = screen_module_level(source)
    if why:
        return [], [f"{rel}: skipped runtime probes ({'; '.join(why[:3])})"]
    by_sym = {u.symbol: u for u in units}
    funcs = [f for f in _functions(source) if f.name in by_sym]
    if not funcs:
        return [], [f"{rel}: no plain top-level typed functions"]
    mod, roots = module_info(repo, rel)
    root = temp_root("cdebt-rt-")
    probes: list[Probe] = []
    try:
        copy_tree(repo, root)
        (root / rel).write_text(source)  # exercise exactly the text we hashed
        rng = random.Random(seed)
        for fn in funcs:
            fn_why = screen_function(source, fn.name)
            if fn_why:
                skips.append(f"{rel}::{fn.name}: skipped ({'; '.join(fn_why[:3])})")
                continue
            a = fn.args
            if a.vararg or a.kwarg or a.kwonlyargs or a.posonlyargs or fn.returns is None:
                skips.append(f"{rel}::{fn.name}: unsupported signature")
                continue
            got = 0
            tries = 0
            while got < per_unit and tries < per_unit * 3:
                tries += 1
                try:
                    vals = [gen_value(x.annotation, rng) for x in a.args]
                except Unsupported as e:
                    skips.append(f"{rel}::{fn.name}: {e}")
                    break
                args_repr = repr(tuple(vals))
                call = f"{fn.name}({', '.join(repr(v) for v in vals)})"
                res = run_in_sandbox(
                    {
                        "mode": "call",
                        "module": mod,
                        "func": fn.name,
                        "args_repr": args_repr,
                        "sys_path": [str(root / r) for r in roots],
                    },
                    root=root,
                    timeout=10,
                )
                pl = res.payload or {}
                if res.timed_out or res.violations or "error" in pl or "results" not in pl:
                    skips.append(
                        f"{rel}::{fn.name}: not safely executable "
                        f"({pl.get('error') or res.violations or 'timeout'})"
                    )
                    break
                r1, r2 = pl["results"][0], pl["results"][1]  # type: ignore[index]
                if r1 != r2:
                    skips.append(f"{rel}::{fn.name}: nondeterministic")
                    break
                kind, value = r1
                if kind == "ok":
                    try:
                        ast.literal_eval(value)
                    except (ValueError, SyntaxError):
                        skips.append(f"{rel}::{fn.name}: non-literal return value")
                        break
                    correct = value
                    wrong = distractors(ast.literal_eval(value), rng)
                else:
                    correct = f"raises {value}"
                    wrong = [f"raises {e}" for e in EXC if e != value] + ["returns None"]
                if call in {p.question for p in probes}:
                    continue
                opts = shuffled([correct, *wrong[:3]], rng)
                if len(opts) < 2:
                    skips.append(f"{rel}::{fn.name}: could not build distractors")
                    break
                q = f"What does `{call}` evaluate to?"
                probes.append(
                    Probe(
                        id=probe_id("runtime", rel, fn.name, q, seed),
                        kind="runtime",
                        path=rel,
                        symbol=fn.name,
                        question=q,
                        options=opts,
                        answer=correct,
                        verified_by="sandbox-exec",
                        seed=seed,
                        evidence={
                            "isolation": res.isolation,
                            "args": args_repr,
                            "result": value,
                            "deterministic_runs": 2,
                        },
                    )
                )
                got += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return probes, skips


def evidence_json(p: Probe) -> str:
    return json.dumps(p.evidence, sort_keys=True)
