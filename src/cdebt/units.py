"""Split a file into understanding units (function/method + module residual) and hash them.

Python and TypeScript/JavaScript use tree-sitter; any other language is one `<file>` unit.
Hash = sha256 of normalised text (CRLF->LF, trailing whitespace stripped, trailing blank
lines dropped). Comments are INCLUDED: a changed comment/docstring can change meaning.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any

from cdebt.models import CodeUnit

PY_EXT = {".py"}
TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def normalise(lines: list[str]) -> str:
    out = [ln.rstrip().replace("\r", "") for ln in lines]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def hash_lines(lines: list[str]) -> str:
    return hashlib.sha256(normalise(lines).encode("utf-8")).hexdigest()


def _parser(ext: str) -> Any:
    import tree_sitter_python
    import tree_sitter_typescript
    from tree_sitter import Language, Parser

    if ext in PY_EXT:
        return Parser(Language(tree_sitter_python.language()))
    lang = (
        tree_sitter_typescript.language_tsx()
        if ext in {".tsx", ".jsx"}
        else tree_sitter_typescript.language_typescript()
    )
    return Parser(Language(lang))


def _name(node: Any) -> str:
    n = node.child_by_field_name("name")
    return n.text.decode("utf-8") if n is not None else "<anon>"


_PY_CONTAINERS = {"class_definition"}
_PY_FUNCS = {"function_definition"}


def _walk_py(node: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    for ch in node.children:
        t = ch.type
        if t == "decorated_definition":
            inner = ch.child_by_field_name("definition")
            if inner is not None and inner.type in _PY_FUNCS | _PY_CONTAINERS:
                yield from _emit_py(inner, ch, prefix)
        elif t in _PY_FUNCS | _PY_CONTAINERS:
            yield from _emit_py(ch, ch, prefix)


def _emit_py(defn: Any, span: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    q = f"{prefix}{_name(defn)}"
    if defn.type in _PY_FUNCS:
        yield q, span
    else:  # class: recurse into body for methods; class header lines go to <module>
        body = defn.child_by_field_name("body")
        if body is not None:
            yield from _walk_py(body, q + ".")


_TS_FUNCS = {"function_declaration", "generator_function_declaration", "method_definition"}


def _walk_ts(node: Any, prefix: str) -> Iterator[tuple[str, Any]]:
    for ch in node.children:
        t = ch.type
        inner = ch
        if t == "export_statement":
            d = ch.child_by_field_name("declaration")
            if d is None:
                continue
            inner = d
            t = d.type
        if t in _TS_FUNCS:
            yield f"{prefix}{_name(inner)}", ch
        elif t in {"class_declaration", "abstract_class_declaration"}:
            body = inner.child_by_field_name("body")
            if body is not None:
                yield from _walk_ts(body, f"{prefix}{_name(inner)}.")
        elif t in {"lexical_declaration", "variable_declaration"}:
            for d in inner.children:
                v = d.child_by_field_name("value") if d.type == "variable_declarator" else None
                if v is not None and v.type in {
                    "arrow_function",
                    "function_expression",
                    "function",
                }:
                    yield f"{prefix}{_name(d)}", ch
        elif t == "method_definition":
            yield f"{prefix}{_name(inner)}", ch


def extract_units(path: str, source: str) -> list[CodeUnit]:
    """Units partition every line of the file: functions/methods + one `<module>` residual."""
    lines = source.splitlines()
    n = len(lines)
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    spans: list[tuple[str, int, int]] = []
    if ext in PY_EXT | TS_EXT and n:
        try:
            tree = _parser(ext).parse(source.encode("utf-8"))
            walker = _walk_py if ext in PY_EXT else _walk_ts
            for sym, node in walker(tree.root_node, ""):
                spans.append((sym, node.start_point[0] + 1, min(node.end_point[0] + 1, n)))
        except Exception:  # parse failure -> file-level fallback  # noqa: BLE001
            spans = []
        if not spans and n:  # no functions: single file unit keeps things simple
            return [
                CodeUnit(
                    path=path, symbol="<module>", ranges=[(1, n)], content_hash=hash_lines(lines)
                )
            ]
    elif n:
        return [
            CodeUnit(path=path, symbol="<file>", ranges=[(1, n)], content_hash=hash_lines(lines))
        ]
    else:
        return []

    units: list[CodeUnit] = []
    taken = [False] * (n + 2)
    seen: dict[str, int] = {}
    for sym, a, b in sorted(spans, key=lambda s: s[1]):
        seen[sym] = seen.get(sym, 0) + 1
        name = sym if seen[sym] == 1 else f"{sym}#{seen[sym]}"  # overloads / redefinitions
        units.append(
            CodeUnit(
                path=path, symbol=name, ranges=[(a, b)], content_hash=hash_lines(lines[a - 1 : b])
            )
        )
        for i in range(a, b + 1):
            taken[i] = True
    residual: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(1, n + 2):
        free = i <= n and not taken[i]
        if free and start is None:
            start = i
        elif not free and start is not None:
            residual.append((start, i - 1))
            start = None
    if residual:
        text = [lines[x - 1] for a, b in residual for x in range(a, b + 1)]
        units.append(
            CodeUnit(path=path, symbol="<module>", ranges=residual, content_hash=hash_lines(text))
        )
    return units


def unit_for_line(units: list[CodeUnit], line: int) -> CodeUnit | None:
    for u in units:
        for a, b in u.ranges:
            if a <= line <= b:
                return u
    return None
