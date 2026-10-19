"""Minimal, dependency-free SCIP index reader (protobuf wire format) + indexer runner.

Fields used (scip.proto, UNVERIFIED against the .proto but VERIFIED on a real scip-python
index, see tests/fixtures/scip/): Index{documents=2}; Document{relative_path=1,
occurrences=2}; Occurrence{range=1 (packed int32), symbol=2, symbol_roles=3 (bit 0x1 =
definition)}. Unknown fields are skipped, so newer indexes still parse.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFINITION = 0x1


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7


def _fields(b: bytes) -> list[tuple[int, int, int | bytes]]:
    i = 0
    out: list[tuple[int, int, int | bytes]] = []
    while i < len(b):
        key, i = _varint(b, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
            out.append((fn, wt, v))
        elif wt == 2:
            n, i = _varint(b, i)
            out.append((fn, wt, b[i : i + n]))
            i += n
        elif wt == 1:
            i += 8
        elif wt == 5:
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
    return out


@dataclass(frozen=True)
class Occurrence:
    line: int  # 1-indexed start line
    symbol: str
    is_definition: bool


@dataclass
class ScipIndex:
    docs: dict[str, list[Occurrence]] = field(default_factory=dict)

    def definitions(self, path: str) -> dict[str, int]:
        return {o.symbol: o.line for o in self.docs.get(path, []) if o.is_definition}

    def references_to(self, symbol: str) -> list[tuple[str, int]]:
        return [
            (p, o.line)
            for p, occs in self.docs.items()
            for o in occs
            if o.symbol == symbol and not o.is_definition
        ]

    def definition_site(self, symbol: str) -> tuple[str, int] | None:
        for p, occs in self.docs.items():
            for o in occs:
                if o.symbol == symbol and o.is_definition:
                    return p, o.line
        return None


def _occurrence(b: bytes) -> Occurrence | None:
    rng: list[int] = []
    sym, roles = "", 0
    for fn, wt, v in _fields(b):
        if fn == 1 and wt == 2 and isinstance(v, bytes):
            i = 0
            while i < len(v):
                x, i = _varint(v, i)
                rng.append(x)
        elif fn == 2 and isinstance(v, bytes):
            sym = v.decode("utf-8", "replace")
        elif fn == 3 and isinstance(v, int):
            roles = v
    if not rng or not sym or sym.startswith("local "):
        return None
    return Occurrence(rng[0] + 1, sym, bool(roles & DEFINITION))


def parse_index(data: bytes, prefix: str = "") -> ScipIndex:
    idx = ScipIndex()
    for fn, _wt, v in _fields(data):
        if fn != 2 or not isinstance(v, bytes):
            continue
        path, occs = "", []
        for dfn, _dwt, dv in _fields(v):
            if dfn == 1 and isinstance(dv, bytes):
                path = dv.decode("utf-8", "replace")
            elif dfn == 2 and isinstance(dv, bytes):
                o = _occurrence(dv)
                if o:
                    occs.append(o)
        if path:
            idx.docs[prefix + path] = occs
    return idx


def load_index(path: Path, prefix: str = "") -> ScipIndex:
    return parse_index(path.read_bytes(), prefix)


LANG_EXT: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "typescript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts"),
}


def index_path(repo: Path) -> Path:
    """The (legacy / Python) index file."""
    return repo / ".cdebt" / "cache" / "index.scip"


def meta_path(repo: Path) -> Path:
    return repo / ".cdebt" / "cache" / "index.meta.json"


def fingerprint(repo: Path, extensions: tuple[str, ...]) -> str:
    """Hash of working-tree source files; an index is only trusted if this still matches."""
    import hashlib

    from cdebt import gitutil

    h = hashlib.sha256()
    for rel in sorted(gitutil.ls_files(repo)):
        if rel.endswith(extensions) and (repo / rel).is_file():
            h.update(rel.encode() + b"\0" + hashlib.sha256((repo / rel).read_bytes()).digest())
    return h.hexdigest()


def _meta(repo: Path) -> dict[str, Any]:
    import json

    try:
        data = json.loads(meta_path(repo).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def index_files(repo: Path) -> list[tuple[Path, str, str]]:
    """(file, path prefix, language) for every existing index of this repo."""
    meta = _meta(repo)
    out: list[tuple[Path, str, str]] = []
    for pr in meta.get("projects", []):
        f = repo / ".cdebt" / "cache" / str(pr.get("file"))
        if f.exists():
            out.append((f, str(pr.get("prefix", "")), str(pr.get("language", "python"))))
    if not out and index_path(repo).exists():  # legacy single Python index
        out.append((index_path(repo), "", "python"))
    return out


def load_all(repo: Path) -> ScipIndex:
    """All of the repo's indexes merged (Python and TypeScript projects)."""
    merged = ScipIndex()
    for f, prefix, _lang in index_files(repo):
        merged.docs.update(load_index(f, prefix).docs)
    return merged


def index_is_fresh(repo: Path, extensions: tuple[str, ...]) -> bool:
    files = index_files(repo)
    if not files:
        return False
    meta = _meta(repo)
    fps = meta.get("fingerprints")
    try:
        if isinstance(fps, dict):
            return all(
                fps.get(lang) == fingerprint(repo, LANG_EXT.get(lang, extensions))
                for lang in {lang for _f, _p, lang in files}
            )
        return bool(meta.get("fingerprint") == fingerprint(repo, extensions))  # legacy meta
    except OSError:
        return False


def _tsconfig_dirs(repo: Path) -> list[str]:
    """Directories (relative, '' = root) holding a tsconfig.json, shallowest first, max 6."""
    from cdebt import gitutil

    dirs = sorted(
        {
            str(Path(p).parent).replace("\\", "/").lstrip(".")
            for p in gitutil.ls_files(repo)
            if Path(p).name == "tsconfig.json"
        },
        key=lambda d: (d.count("/"), d),
    )
    return [d.strip("/") for d in dirs][:6]


def _run(cmd: list[str], cwd: Path, timeout: int) -> bool:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def build_indexes(
    repo: Path, languages: tuple[str, ...] = ("python",), timeout: int = 600
) -> list[Path]:
    """Run the SCIP indexers (optional external tools; need node/npx). Writes only under
    .cdebt/cache/ (a TypeScript project without a tsconfig is indexed in a TEMP COPY so the
    repo itself is never modified). Returns the index files written."""
    import json

    npx = shutil.which("npx")
    if npx is None:
        return []
    cache = repo / ".cdebt" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    meta = _meta(repo)
    projects: list[dict[str, str]] = [
        p for p in meta.get("projects", []) if p.get("language") not in languages
    ]
    fps: dict[str, str] = dict(meta.get("fingerprints", {}))
    written: list[Path] = []
    if "python" in languages:
        out = index_path(repo)
        cmd = [
            npx,
            "--yes",
            "@sourcegraph/scip-python",
            "index",
            "--project-name",
            repo.name,
            "--project-version",
            "0",
            "--output",
            str(out),
            ".",
        ]
        if _run(cmd, repo, timeout) and out.exists():
            projects.append({"file": out.name, "prefix": "", "language": "python"})
            fps["python"] = fingerprint(repo, LANG_EXT["python"])
            written.append(out)
    if "typescript" in languages:
        dirs = _tsconfig_dirs(repo)
        ok_any = False
        if dirs:
            for k, d in enumerate(dirs):
                out = cache / f"index.typescript.{k}.scip"
                cwd = repo / d if d else repo
                cmd = [npx, "--yes", "@sourcegraph/scip-typescript", "index", "--output", str(out)]
                if _run(cmd, cwd, timeout) and out.exists():
                    projects.append(
                        {"file": out.name, "prefix": d + "/" if d else "", "language": "typescript"}
                    )
                    written.append(out)
                    ok_any = True
        else:  # no tsconfig: infer one inside a temp copy (the tool writes tsconfig.json)
            import tempfile

            from cdebt.probes.base import copy_tree

            tmp = Path(tempfile.mkdtemp(prefix="cdebt-scip-ts-")).resolve()
            try:
                copy_tree(repo, tmp)
                out = cache / "index.typescript.0.scip"
                cmd = [
                    npx,
                    "--yes",
                    "@sourcegraph/scip-typescript",
                    "index",
                    "--infer-tsconfig",
                    "--output",
                    str(out),
                ]
                if _run(cmd, tmp, timeout) and out.exists():
                    projects.append({"file": out.name, "prefix": "", "language": "typescript"})
                    written.append(out)
                    ok_any = True
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if ok_any:
            fps["typescript"] = fingerprint(repo, LANG_EXT["typescript"])
    if written:
        meta_path(repo).write_text(json.dumps({"projects": projects, "fingerprints": fps}))
    return written


def build_index(repo: Path, language: str = "python", timeout: int = 600) -> Path | None:
    """Back-compat wrapper: index one language, return the first file written."""
    w = build_indexes(repo, (language,), timeout)
    return w[0] if w else None
