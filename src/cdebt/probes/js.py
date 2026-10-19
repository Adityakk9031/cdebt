"""TypeScript / JavaScript probes (Node >= 24). Same rules as the Python probes: the answer is
the result of EXECUTION in a sandboxed Node subprocess (permission model: no fs writes, no child
processes, no workers; network entry points disabled and blocked by the OS layer / Docker), run
twice to prove determinism; functions and modules with side effects are skipped. Structure comes
from tree-sitter; argument values come from TypeScript type annotations (or JSDoc `@param {T}`).

Not supported (skipped with a reason): JSX/TSX, TypeScript enums/namespaces (not erasable),
imports Node cannot resolve (extensionless relative imports, path aliases, npm packages),
generics and destructured parameters, classes/methods, generators."""

from __future__ import annotations

import ast
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any, cast

from cdebt.models import CodeUnit, Probe
from cdebt.probes.base import copy_tree, probe_id, shuffled, temp_root
from cdebt.probes.runtime import WORDS, distractors
from cdebt.sandbox import SandboxUnavailable, run_in_sandbox

JS_EXT = (".ts", ".mts", ".js", ".mjs", ".cjs")
UNSUPPORTED_EXT = (".tsx", ".jsx")
MIN_NODE = 24
BANNED_MODULES = {
    "fs",
    "fs/promises",
    "os",
    "child_process",
    "net",
    "http",
    "https",
    "http2",
    "tls",
    "dns",
    "dgram",
    "crypto",
    "worker_threads",
    "cluster",
    "vm",
    "readline",
    "process",
    "perf_hooks",
    "timers",
    "v8",
    "repl",
    "inspector",
    "module",
    "wasi",
    "tty",
}
SIDE_EFFECT_IDENTS = {
    "require",
    "process",
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "Worker",
    "eval",
    "Function",
    "setTimeout",
    "setInterval",
    "setImmediate",
    "WebAssembly",
}
NONDET_IDENTS = {"Date", "performance", "crypto", "globalThis", "Atomics", "SharedArrayBuffer"}
BANNED_IDENTS = SIDE_EFFECT_IDENTS | NONDET_IDENTS
SAFE_TOP = {
    "import_statement",
    "export_statement",
    "function_declaration",
    "class_declaration",
    "lexical_declaration",
    "variable_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "comment",
    "empty_statement",
    "abstract_class_declaration",
}
UNDEF = {"$undef": 1}


class Unsupported(Exception):
    pass


def node_version() -> int | None:
    exe = shutil.which("node")
    if exe is None:
        return None
    import subprocess

    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except Exception:  # noqa: BLE001
        return None


def node_ok() -> tuple[bool, str]:
    v = node_version()
    if v is None:
        return False, "node is not installed"
    if v < MIN_NODE:
        return False, f"node >= {MIN_NODE} required (found {v})"
    return True, ""


def _parser(path: str) -> Any:
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    lang = (
        tree_sitter_typescript.language_tsx()
        if path.endswith((".tsx", ".jsx"))
        else tree_sitter_typescript.language_typescript()
    )
    return Parser(Language(lang))


def _text(n: Any) -> str:
    return bytes(n.text).decode("utf-8", "replace") if n is not None else ""


def _specifier(n: Any) -> str:
    return _text(n).strip("'\"`").removeprefix("node:")


def _import_aliases(root: Any) -> dict[str, str]:
    """local name -> module specifier, for ES imports and `const x = require('m')`."""
    out: dict[str, str] = {}

    def walk(n: Any) -> None:
        if n.type == "import_statement":
            src = n.child_by_field_name("source")
            mod = _specifier(src)
            for d in _descend(n):
                if d.type in ("identifier", "namespace_import", "import_specifier"):
                    for ident in [d] if d.type == "identifier" else _descend(d):
                        if ident.type == "identifier":
                            out[_text(ident)] = mod
        elif n.type == "variable_declarator":
            v = n.child_by_field_name("value")
            if v is not None and v.type == "call_expression":
                f = v.child_by_field_name("function")
                args = v.child_by_field_name("arguments")
                if (
                    f is not None
                    and _text(f) == "require"
                    and args is not None
                    and args.named_children
                ):
                    nm = n.child_by_field_name("name")
                    if nm is not None and nm.type == "identifier":
                        out[_text(nm)] = _specifier(args.named_children[0])
        for c in n.children:
            walk(c)

    walk(root)
    return out


def _descend(n: Any) -> list[Any]:
    out: list[Any] = []
    stack = list(n.children)
    while stack:
        x = stack.pop()
        out.append(x)
        stack.extend(x.children)
    return out


def _screen_node(n: Any, aliases: dict[str, str], *, strict: bool = True) -> list[str]:
    """Reasons the code under node `n` is not safe/deterministic to execute for a probe."""
    why: set[str] = set()
    for d in [n, *_descend(n)]:
        t = d.type
        if t == "identifier":
            name = _text(d)
            if name in (BANNED_IDENTS if strict else SIDE_EFFECT_IDENTS):
                why.add(f"uses {name}")
            mod = aliases.get(name)
            if mod is not None and mod in BANNED_MODULES:
                why.add(f"uses {mod} (side effects)")
        elif t == "member_expression":
            o, p = d.child_by_field_name("object"), d.child_by_field_name("property")
            if strict and _text(o) == "Math" and _text(p) == "random":
                why.add("uses Math.random")
        elif t == "import" or (
            t == "call_expression" and _text(d.child_by_field_name("function")) == "import"
        ):
            why.add("dynamic import")
        elif t == "await_expression" and n.type != "function_declaration":
            pass  # allowed inside async functions; top-level await is screened by the caller
    return sorted(why)


def module_screen(root: Any, aliases: dict[str, str]) -> list[str]:
    """Reasons the module must NOT be imported for probing (it would run code on import)."""
    why: set[str] = set()
    for ch in root.children:
        if ch.type not in SAFE_TOP:
            if (
                ch.type == "expression_statement"
                and ch.named_children
                and ch.named_children[0].type == "string"
            ):
                continue  # "use strict"
            why.add(f"top-level {ch.type} runs code on import")
            continue
        if ch.type in ("lexical_declaration", "variable_declaration"):
            for d in ch.named_children:
                v = d.child_by_field_name("value")
                if v is not None and v.type not in ("arrow_function", "function_expression"):
                    why.update(_screen_node(v, aliases, strict=False))
    return sorted(why)


# ---- type annotations -> argument values -------------------------------------------------
def _type_of_param(p: Any) -> Any | None:
    t = p.child_by_field_name("type")
    if t is not None and t.named_children:
        return t.named_children[0]
    return None


def _alias_map(root: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ch in root.children:
        n = ch.child_by_field_name("declaration") if ch.type == "export_statement" else ch
        if n is not None and n.type == "type_alias_declaration":
            nm, v = n.child_by_field_name("name"), n.child_by_field_name("value")
            if nm is not None and v is not None:
                out[_text(nm)] = v
        elif n is not None and n.type == "interface_declaration":
            nm, body = n.child_by_field_name("name"), n.child_by_field_name("body")
            if nm is not None and body is not None:
                out[_text(nm)] = body
    return out


def gen_value(t: Any, rng: random.Random, aliases: dict[str, Any], depth: int = 0) -> Any:
    if t is None or depth > 4:
        raise Unsupported("type")
    k = t.type
    if k == "parenthesized_type" and t.named_children:
        return gen_value(t.named_children[0], rng, aliases, depth + 1)
    if k == "predefined_type":
        n = _text(t)
        if n == "number":
            return rng.choice(
                [rng.randint(-5, 20), rng.randint(0, 9), rng.choice([0.5, 1.5, -2.25])]
            )
        if n == "string":
            return rng.choice(WORDS)
        if n == "boolean":
            return rng.random() < 0.5
        raise Unsupported(f"type {n}")
    if k == "literal_type":
        txt = _text(t)
        if txt in ("null",):
            return None
        if txt == "undefined":
            return UNDEF
        if txt in ("true", "false"):
            return txt == "true"
        try:
            return json.loads(txt)
        except ValueError:
            return txt.strip("'\"`")
    if k == "array_type" and t.named_children:
        return [
            gen_value(t.named_children[0], rng, aliases, depth + 1)
            for _ in range(rng.randint(0, 4))
        ]
    if k == "generic_type":
        name = _text(t.child_by_field_name("name"))
        targs = t.child_by_field_name("type_arguments")
        args = targs.named_children if targs is not None else []
        if name in ("Array", "ReadonlyArray") and len(args) == 1:
            return [gen_value(args[0], rng, aliases, depth + 1) for _ in range(rng.randint(0, 4))]
        if name == "Record" and len(args) == 2:
            return {
                rng.choice(WORDS[:5]) + str(i): gen_value(args[1], rng, aliases, depth + 1)
                for i in range(rng.randint(0, 3))
            }
        raise Unsupported(f"generic {name}")
    if k == "union_type":
        members: list[Any] = []

        def flat(n: Any) -> None:
            if n.type == "union_type":
                for c in n.named_children:
                    flat(c)
            else:
                members.append(n)

        flat(t)
        nullish = [m for m in members if _text(m) in ("null", "undefined")]
        rest = [m for m in members if m not in nullish]
        if nullish and rng.random() < 0.2:
            return None if _text(rng.choice(nullish)) == "null" else UNDEF
        if not rest:
            raise Unsupported("union")
        return gen_value(rng.choice(rest), rng, aliases, depth + 1)
    if k == "tuple_type":
        return [gen_value(c, rng, aliases, depth + 1) for c in t.named_children]
    if k in ("object_type", "interface_body"):
        obj: dict[str, Any] = {}
        for pr in t.named_children:
            if pr.type != "property_signature":
                raise Unsupported("object member")
            nm = pr.child_by_field_name("name")
            ty = _type_of_param(pr)
            optional = any(c.type == "?" for c in pr.children)
            if optional and rng.random() < 0.4:
                continue
            obj[_text(nm).strip("'\"")] = gen_value(ty, rng, aliases, depth + 1)
        return obj
    if k == "type_identifier":
        name = _text(t)
        if name in aliases:
            return gen_value(aliases[name], rng, aliases, depth + 1)
        raise Unsupported(f"type {name}")
    raise Unsupported(f"type {k}")


_JSDOC_PARAM = re.compile(r"@param\s+\{([^}]+)\}\s+(?:\[)?(\w+)")


def _jsdoc_types(source: str, node: Any) -> dict[str, str]:
    """JSDoc `@param {T} name` of the comment immediately before `node` (JS files)."""
    start = node.start_byte
    before = source.encode("utf-8")[:start].decode("utf-8", "replace").rstrip()
    if not before.endswith("*/"):
        return {}
    block = before[before.rfind("/**") :] if "/**" in before else ""
    return {n: t for t, n in _JSDOC_PARAM.findall(block)}


def _jsdoc_value(t: str, rng: random.Random) -> Any:
    t = t.strip().lstrip("?").rstrip("=").strip()
    if t == "number":
        return rng.randint(-5, 20)
    if t == "string":
        return rng.choice(WORDS)
    if t == "boolean":
        return rng.random() < 0.5
    m = re.fullmatch(r"(?:Array<(\w+)>|(\w+)\[\])", t)
    if m:
        inner = m.group(1) or m.group(2)
        return [_jsdoc_value(inner, rng) for _ in range(rng.randint(0, 4))]
    raise Unsupported(f"jsdoc type {t}")


def to_js_literal(v: Any) -> str:
    if v is UNDEF or v == UNDEF:
        return "undefined"
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(to_js_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(k)}: {to_js_literal(x)}" for k, x in v.items()) + "}"
    raise Unsupported("literal")


def js_distractors(answer: str, rng: random.Random) -> list[str]:
    try:
        val = json.loads(answer)
    except ValueError:
        pool = {
            "undefined": ["null", "0", '""', "false"],
            "NaN": ["0", "undefined", "null", "Infinity"],
            "null": ["undefined", "0", '""', "[]"],
        }
        if answer in pool:
            return pool[answer]
        if re.fullmatch(r"-?\d+n", answer):
            n = int(answer[:-1])
            return [f"{n + 1}n", f"{n - 1}n", str(n), f"{-n}n"]
        return ["undefined", "null", "0"]
    out: list[str] = []
    for d in distractors(val, rng):
        try:
            lit = to_js_literal(ast.literal_eval(d))
        except (ValueError, SyntaxError, Unsupported):
            continue
        if lit != answer and lit not in out:
            out.append(lit)
    return out


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


# ---- candidates --------------------------------------------------------------------------
class Fn:
    def __init__(self, name: str, node: Any, params: Any, exported: bool, decl: Any) -> None:
        self.name, self.node, self.params, self.exported, self.decl = (
            name,
            node,
            params,
            exported,
            decl,
        )


def functions(root: Any) -> list[Fn]:
    out: list[Fn] = []
    for ch in root.children:
        exported = ch.type == "export_statement"
        n = ch.child_by_field_name("declaration") if exported else ch
        if n is None:
            continue
        if n.type == "function_declaration":
            nm = n.child_by_field_name("name")
            if nm is not None:
                out.append(Fn(_text(nm), n, n.child_by_field_name("parameters"), exported, ch))
        elif n.type in ("lexical_declaration", "variable_declaration"):
            for d in n.named_children:
                v = d.child_by_field_name("value")
                nm = d.child_by_field_name("name")
                if (
                    v is not None
                    and v.type in ("arrow_function", "function_expression")
                    and nm is not None
                    and nm.type == "identifier"
                ):
                    params = v.child_by_field_name("parameters")
                    out.append(Fn(_text(nm), v, params, exported, ch))
    return out


def constants(root: Any, aliases: dict[str, str]) -> list[tuple[str, int, bool, bool]]:
    """(name, line, computed, exported) for top-level `const NAME = expr`."""
    out: list[tuple[str, int, bool, bool]] = []
    for ch in root.children:
        exported = ch.type == "export_statement"
        n = ch.child_by_field_name("declaration") if exported else ch
        if n is None or n.type != "lexical_declaration" or not _text(n).startswith("const"):
            continue
        for d in n.named_children:
            nm, v = d.child_by_field_name("name"), d.child_by_field_name("value")
            if (
                v is None
                or nm is None
                or nm.type != "identifier"
                or v.type
                in (
                    "arrow_function",
                    "function_expression",
                    "class",
                    "call_expression" if False else "_none_",
                )
            ):
                continue
            if _screen_node(v, aliases):
                continue
            literal = v.type in (
                "number",
                "string",
                "true",
                "false",
                "null",
                "template_string",
            ) or (
                v.type in ("array", "object")
                and not any(
                    x.type
                    in ("call_expression", "identifier", "binary_expression", "member_expression")
                    for x in _descend(v)
                )
            )
            out.append((_text(nm), nm.start_point[0] + 1, not literal, exported))
    return out


def _mod_source(source: str, rel: str, need: list[str], exported: set[str], root: Any) -> str:
    """Source with `export { ... }` appended for needed non-exported names (ESM-style files)."""
    todo = [n for n in dict.fromkeys(need) if n not in exported]
    if not todo:
        return source
    esm = rel.endswith((".ts", ".mts", ".mjs")) or any(
        c.type in ("import_statement", "export_statement") for c in root.children
    )
    if not esm:
        return source
    return source + "\nexport { " + ", ".join(todo) + " };\n"


def _exported_names(root: Any) -> set[str]:
    out: set[str] = set()
    for ch in root.children:
        if ch.type != "export_statement":
            continue
        n = ch.child_by_field_name("declaration")
        if n is not None:
            nm = n.child_by_field_name("name")
            if nm is not None:
                out.add(_text(nm))
            for d in n.named_children:
                if d.type == "variable_declarator":
                    nm2 = d.child_by_field_name("name")
                    if nm2 is not None:
                        out.add(_text(nm2))
        else:
            for d in _descend(ch):
                if d.type == "export_specifier":
                    nm3 = d.child_by_field_name("name")
                    if nm3 is not None:
                        out.add(_text(nm3))
    return out


def _prepare(
    repo: Path, rel: str, source: str, units: list[CodeUnit]
) -> tuple[Any, dict[str, str], dict[str, Any]] | str:
    if not rel.endswith(JS_EXT):
        return f"{rel}: not a supported JS/TS file" + (
            " (JSX/TSX unsupported)" if rel.endswith(UNSUPPORTED_EXT) else ""
        )
    ok, why = node_ok()
    if not ok:
        return f"{rel}: {why}"
    tree = _parser(rel).parse(source.encode("utf-8"))
    root = tree.root_node
    if root.has_error and "error" in " ".join(c.type for c in root.children).lower():
        return f"{rel}: does not parse as TypeScript"
    aliases = _import_aliases(root)
    bad = module_screen(root, aliases)
    if bad:
        return f"{rel}: skipped ({'; '.join(bad[:3])})"
    return root, aliases, _alias_map(root)


def generate_runtime(
    repo: Path, rel: str, source: str, units: list[CodeUnit], seed: int, per_unit: int = 2
) -> tuple[list[Probe], list[str]]:
    prep = _prepare(repo, rel, source, units)
    if isinstance(prep, str):
        return [], [prep]
    root, aliases, type_aliases = prep
    by_sym = {u.symbol for u in units}
    fns = [f for f in functions(root) if f.name in by_sym]
    if not fns:
        return [], [f"{rel}: no top-level functions"]
    exported = _exported_names(root)
    rng = random.Random(seed)
    skips: list[str] = []
    cases: list[tuple[Fn, list[Any], str]] = []
    for fn in fns:
        why = _screen_node(fn.node, aliases)
        if why:
            skips.append(f"{rel}::{fn.name}: skipped ({'; '.join(why[:3])})")
            continue
        if fn.node.type == "function_declaration" and any(c.type == "*" for c in fn.node.children):
            skips.append(f"{rel}::{fn.name}: generator")
            continue
        if fn.params is None:
            skips.append(f"{rel}::{fn.name}: unsupported signature")
            continue
        params = [c for c in fn.params.named_children if c.type != "comment"]
        if any(p.type not in ("required_parameter", "optional_parameter") for p in params):
            skips.append(f"{rel}::{fn.name}: unsupported signature")
            continue
        if any(
            p.child_by_field_name("pattern") is None
            or p.child_by_field_name("pattern").type != "identifier"
            for p in params
        ):
            skips.append(f"{rel}::{fn.name}: destructured parameter")
            continue
        jsdoc = _jsdoc_types(source, fn.decl) if not rel.endswith((".ts", ".mts")) else {}
        for _ in range(per_unit):
            try:
                args = []
                for p in params:
                    ty = _type_of_param(p)
                    pname = _text(p.child_by_field_name("pattern"))
                    if ty is not None:
                        args.append(gen_value(ty, rng, type_aliases))
                    elif pname in jsdoc:
                        args.append(_jsdoc_value(jsdoc[pname], rng))
                    else:
                        raise Unsupported("missing type")
                call = f"{fn.name}({', '.join(to_js_literal(a) for a in args)})"
            except Unsupported as e:
                skips.append(f"{rel}::{fn.name}: {e}")
                break
            if any(c[2] == call for c in cases):
                continue
            cases.append((fn, args, call))
    if not cases:
        return [], skips
    mod_src = _mod_source(
        source, rel, list(dict.fromkeys(c[0].name for c in cases)), exported, root
    )
    rootdir = temp_root("cdebt-js-")
    probes: list[Probe] = []
    try:
        copy_tree(repo, rootdir)
        (rootdir / rel).write_text(mod_src)
        for fn, args, call in cases:
            try:
                res = run_in_sandbox(
                    {
                        "mode": "call",
                        "module": str(rootdir / rel),
                        "func": fn.name,
                        "args": json.dumps(args),
                    },
                    root=rootdir,
                    timeout=10,
                    runtime="node",
                )
            except SandboxUnavailable as e:
                return probes, skips + [f"{rel}: {e}"]
            pl = res.payload or {}
            if res.timed_out or res.violations or "error" in pl or "results" not in pl:
                skips.append(
                    f"{rel}::{fn.name}: not safely executable "
                    f"({pl.get('error') or res.violations or 'timeout'})"
                )
                continue
            results = cast(list[Any], pl["results"])
            r1, r2 = results[0], results[1]
            if r1 != r2:
                skips.append(f"{rel}::{fn.name}: nondeterministic")
                continue
            kind, value = r1
            if kind == "ok":
                if len(value) > 120:
                    skips.append(f"{rel}::{fn.name}: result too long")
                    continue
                correct, wrong = value, js_distractors(value, rng)
            else:
                correct = f"throws {value}"
                wrong = [
                    f"throws {e}" for e in ("TypeError", "RangeError", "Error") if e != value
                ] + ["returns undefined"]
            opts = shuffled([correct, *wrong[:3]], rng)
            if len(opts) < 2:
                skips.append(f"{rel}::{fn.name}: could not build distractors")
                continue
            q = f"What does `{call}` evaluate to?"
            if any(p.question == q for p in probes):
                continue
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
                        "runtime": "node",
                        "args": json.dumps(args),
                        "result": value,
                        "deterministic_runs": 2,
                    },
                )
            )
            if sum(1 for p in probes if p.symbol == fn.name) >= per_unit:
                continue
    finally:
        shutil.rmtree(rootdir, ignore_errors=True)
    # cap per unit
    seen: dict[str, int] = {}
    capped: list[Probe] = []
    for p in probes:
        seen[p.symbol] = seen.get(p.symbol, 0) + 1
        if seen[p.symbol] <= per_unit:
            capped.append(p)
    return capped, skips


def generate_constants(
    repo: Path, rel: str, source: str, units: list[CodeUnit], seed: int, per_unit: int = 2
) -> tuple[list[Probe], list[str]]:
    mod_unit = next((u for u in units if u.symbol == "<module>"), None)
    if mod_unit is None:
        return [], [f"{rel}: no module-level unit"]
    prep = _prepare(repo, rel, source, units)
    if isinstance(prep, str):
        return [], [prep]
    root, aliases, _ = prep
    cands = constants(root, aliases)
    if not cands:
        return [], [f"{rel}::<module>: no environment-independent top-level constants"]
    cands.sort(key=lambda c: (not c[2], c[1]))
    names = [c[0] for c in cands][:12]
    exported = _exported_names(root)
    if not rel.endswith((".ts", ".mts", ".mjs")) and not any(
        c.type in ("import_statement", "export_statement") for c in root.children
    ):
        names = [n for n in names if n in exported]  # CommonJS: cannot inject exports
        if not names:
            return [], [f"{rel}::<module>: CommonJS module without exported constants"]
    mod_src = _mod_source(source, rel, names, exported, root)
    rootdir = temp_root("cdebt-jsc-")
    rng = random.Random(seed)
    probes: list[Probe] = []
    skips: list[str] = []
    try:
        copy_tree(repo, rootdir)
        (rootdir / rel).write_text(mod_src)
        try:
            res = run_in_sandbox(
                {"mode": "attrs", "module": str(rootdir / rel), "names": names},
                root=rootdir,
                timeout=15,
                runtime="node",
            )
        except SandboxUnavailable as e:
            return [], [f"{rel}: {e}"]
        pl = res.payload or {}
        if res.timed_out or res.violations or "error" in pl or "values" not in pl:
            return [], [
                f"{rel}::<module>: not safely importable "
                f"({pl.get('error') or res.violations or 'timeout'})"
            ]
        info = {c[0]: c for c in cands}
        for name in names:
            if len(probes) >= per_unit:
                break
            v1, v2 = cast(dict[str, list[str]], pl["values"])[name]
            if v1 != v2 or v1 == "<unsupported>":
                skips.append(f"{rel}::{name}: unstable or unsupported value")
                continue
            if len(v1) > 120:
                skips.append(f"{rel}::{name}: too long")
                continue
            opts = shuffled([v1, *js_distractors(v1, rng)[:3]], rng)
            if len(opts) < 2:
                skips.append(f"{rel}::{name}: could not build distractors")
                continue
            q = f"After importing {rel}, what is the value of `{name}`?"
            probes.append(
                Probe(
                    id=probe_id("constant", rel, "<module>", q, seed),
                    kind="constant",
                    path=rel,
                    symbol="<module>",
                    question=q,
                    options=opts,
                    answer=v1,
                    verified_by="sandbox-exec",
                    seed=seed,
                    evidence={
                        "isolation": res.isolation,
                        "runtime": "node",
                        "name": name,
                        "line": info[name][1],
                        "computed": info[name][2],
                        "evaluations": 2,
                        "scope": "value of this constant only",
                    },
                )
            )
    finally:
        shutil.rmtree(rootdir, ignore_errors=True)
    return probes, skips


__all__ = ["generate_runtime", "generate_constants", "node_ok", "ast"]
