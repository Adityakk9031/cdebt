"""Isolated execution. Layers (the achieved level is reported on every result):

1. static screen (`screen_source`): refuse files that import/call side-effecting things;
2. child process: `python -I`, temp cwd, scrubbed env, wall-clock timeout, process-group
   kill, RLIMIT_CPU / FSIZE / NOFILE / AS (best effort), parent RSS watchdog;
3. in-child audit hook that BLOCKS (raises PermissionError before the action happens) file
   writes, network, subprocess/fork/exec, ctypes. Policy "tests" allows writes inside the
   sandbox root and subprocesses (needed to run a project's test suite);
4. OS isolation when present: Docker (`--network none`, caps dropped, memory/pids capped) or macOS `sandbox-exec` (network + file-write deny).

THREAT MODEL: accidental or AI-generated side effects, not a determined attacker. Audit hooks
are not a security boundary against hostile native code; use Docker for untrusted input.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os

try:  # POSIX only; Windows is unsupported for execution (see run_in_sandbox)
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Policy = Literal["pure", "tests"]
MARK = "\x00CDEBT-RESULT\x00"

BLOCKED_MODULES = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "random",
    "time",
    "datetime",
    "secrets",
    "urllib",
    "http",
    "requests",
    "httpx",
    "ftplib",
    "smtplib",
    "tempfile",
    "multiprocessing",
    "threading",
    "asyncio",
    "signal",
    "pathlib",
    "io",
    "builtins",
    "importlib",
    "pickle",
    "marshal",
    "glob",
    "fileinput",
    "uuid",
    "platform",
    "select",
    "selectors",
    "sqlite3",
    "webbrowser",
    "pty",
    "fcntl",
    "mmap",
    "resource",
    "gc",
    "inspect",
}
BLOCKED_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "input",
    "exit",
    "quit",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "setattr",
    "delattr",
}


def screen_source(source: str) -> list[str]:
    """Reasons the file must NOT be executed ([] = passes the static screen)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e.msg}"]
    reasons: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in BLOCKED_MODULES:
                    reasons.append(f"imports {a.name} (side effects)")
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[0] in BLOCKED_MODULES and n.level == 0:
                reasons.append(f"imports from {n.module} (side effects)")
        elif (
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in BLOCKED_CALLS
        ):
            reasons.append(f"calls {n.func.id}()")
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            reasons.append("mutates global/nonlocal state")
        elif isinstance(n, (ast.Await, ast.AsyncFunctionDef)):
            reasons.append("async code")
    return sorted(set(reasons))


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """local name -> top-level module, for every import in the file."""
    out: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out[(a.asname or a.name).split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                out[a.asname or a.name] = n.module.split(".")[0]
    return out


def screen_module_level(source: str) -> list[str]:
    """Reasons the module must not be IMPORTED for probing: only module-level (not inside a
    def/class body) calls to side-effecting builtins and global/nonlocal. Importing `os` or
    `pathlib` is fine: actual writes/network/subprocess are blocked by the audit hook."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e.msg}"]
    reasons: set[str] = set()

    def visit(n: ast.AST) -> None:
        for ch in ast.iter_child_nodes(n):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # bodies run only if called
            if (
                isinstance(ch, ast.Call)
                and isinstance(ch.func, ast.Name)
                and ch.func.id in BLOCKED_CALLS
            ):
                reasons.add(f"module-level call to {ch.func.id}()")
            visit(ch)

    visit(tree)
    return sorted(reasons)


def screen_function(source: str, name: str) -> list[str]:
    """Reasons the function `name` is not safe to run for a probe: it touches a side-effecting
    module (os, time, random, pathlib, ...), calls a side-effecting builtin, mutates global
    state, or is async."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"syntax error: {e.msg}"]
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    if fn is None:
        return [f"function {name} not found"]
    aliases = _import_aliases(tree)
    reasons: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            mod = aliases.get(n.id)
            if mod in BLOCKED_MODULES:
                reasons.add(f"uses {mod} (side effects)")
            if isinstance(n.ctx, ast.Load) and n.id in BLOCKED_CALLS:
                reasons.add(f"uses {n.id}()")
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            reasons.add("mutates global/nonlocal state")
        elif isinstance(n, (ast.Await, ast.AsyncFunctionDef)):
            reasons.add("async code")
    return sorted(reasons)


@dataclass
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    killed_for_memory: bool = False
    killed_for_output: bool = False  # produced more than MAX_OUTPUT bytes
    killed_for_procs: bool = False  # spawned more than MAX_PROCS descendants
    violations: list[str] = field(default_factory=list)
    isolation: str = "audit+rlimit"
    payload: dict[str, object] | None = None  # JSON the runner emitted, if any
    elapsed: float = 0.0


_RUNNER = r"""
import sys as _sys


def _run():
    # Everything lives in function scope: nothing for user code to reach through `__main__`,
    # and the guard (`hook`) is created inside `_install()` which returns, so no frame that
    # user code can walk (tracebacks, f_back) holds a reference to it.
    import os, json, io, stat, sysconfig, resource
    spec = json.loads(_sys.stdin.read())
    POLICY = spec["policy"]
    _lstat, _readlink, _getcwd, _fsdecode = os.lstat, os.readlink, os.getcwd, os.fsdecode
    _S_ISLNK, _S_ISREG = stat.S_ISLNK, stat.S_ISREG
    _violations = []

    def realpath(p):
        # Own implementation bound to captured primitives: user code that monkeypatches
        # os.path.realpath / os.lstat / os.readlink cannot make the guard misjudge a path.
        p = _fsdecode(p)
        if not p.startswith("/"):
            p = _getcwd() + "/" + p
        out = []
        stack = [x for x in reversed(p.split("/")) if x]
        hops = 0
        while stack:
            c = stack.pop()
            if c == ".":
                continue
            if c == "..":
                if out:
                    out.pop()
                continue
            cand = "/" + "/".join(out + [c])
            try:
                st = _lstat(cand)
            except OSError:
                out.append(c)
                continue
            if _S_ISLNK(st.st_mode):
                hops += 1
                if hops > 40:
                    return "/__unresolvable__"
                t = _readlink(cand)
                if t.startswith("/"):
                    out = []
                stack.extend(x for x in reversed(t.split("/")) if x)
            else:
                out.append(c)
        return "/" + "/".join(out)

    ROOT = realpath(spec["root"])

    def _install():
        _BLOCK = ("socket.", "ctypes.", "winreg.", "sys.monitoring")
        _PROC = ("subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.exec",
                 "os.posix_spawn", "os.spawn", "os.startfile", "pty.spawn")
        _FS = ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.truncate", "os.symlink",
               "os.link", "os.chmod", "os.chown", "os.utime", "os.mkfifo", "os.mknod", "shutil.",
               "os.removexattr", "os.setxattr", "os.chroot")
        # tampering with the guard itself, and shared memory (creates host-visible segments)
        _TAMPER = ("gc.get_objects", "gc.get_referrers", "gc.get_referents", "sys._getframe",
                   "sys._current_frames", "sys.settrace", "sys.setprofile",
                   "sys.setswitchinterval")
        _NO_IMPORT = ("_posixshmem", "multiprocessing.shared_memory", "_xxsubinterpreters",
                      "_interpreters", "_testcapi", "_testinternalcapi")
        _OK = tuple(r for r in (realpath(x) for x in {
            ROOT, _sys.prefix, _sys.base_prefix, _sys.exec_prefix,
            *sysconfig.get_paths().values(), "/usr", "/System/Library", "/Library/Frameworks",
            "/lib", "/lib64", "/etc/localtime", "/dev/null", "/dev/urandom", "/dev/random",
            "/dev/tty", "/private/var/db/timezone"}) if r)
        _O_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        _PathLike = os.PathLike
        _sep = os.sep

        def read_ok(p, _any=any, _isinstance=isinstance, _str=str, _bytes=bytes):
            if _isinstance(p, int) or p is None:
                return True  # an already-open fd / the cwd (== ROOT)
            try:
                rp = realpath(p if _isinstance(p, (_str, _bytes, _PathLike)) else _str(p))
            except BaseException:
                return False
            if not _any(rp == a or rp.startswith(a + _sep) for a in _OK):
                return False
            if rp.startswith(ROOT + _sep):
                # a hard link inside the root can alias a file outside it (the path check cannot
                # see that); copy_tree never creates one, so a regular file with nlink > 1 is hostile
                try:
                    st = _lstat(rp)
                except OSError:
                    return True
                if _S_ISREG(st.st_mode) and st.st_nlink > 1:
                    return False
            return True

        def inside(p, _isinstance=isinstance, _str=str, _bytes=bytes):
            try:
                rp = realpath(p if _isinstance(p, (_str, _bytes, _PathLike)) else _str(p))
            except BaseException:
                return False
            return rp == ROOT or rp.startswith(ROOT + _sep) or rp in ("/dev/null", "/dev/tty")

        def deny(ev, why, _PermissionError=PermissionError, _violations=_violations):
            _violations.append(ev + ": " + why)
            raise _PermissionError("cdebt sandbox blocked " + ev + " (" + why + ")")

        def hook(event, args, _any=any, _isinstance=isinstance, _str=str, _int=int, _all=all,
                 _PathLike=_PathLike, _bytes=bytes):
            if event.startswith(_BLOCK):
                deny(event, "network/native")
            if event.startswith(_PROC) and POLICY == "pure":
                deny(event, "process creation")
            if event in ("os.kill", "os.killpg", "signal.pthread_kill"):
                deny(event, "signalling other processes")
            if event in _TAMPER:
                deny(event, "introspection that could tamper with the sandbox")
            if event == "import" and args and _str(args[0]) in _NO_IMPORT:
                deny(event, "module not allowed in the sandbox")
            if event == "sqlite3.connect" and args and _str(args[0]) not in (":memory:", ""):
                deny(event, "sqlite3 would open a file in C, outside the audited open()")
            if event == "open":
                path, mode, flags = args
                write = False
                if _isinstance(flags, _int) and flags & _O_WRITE:
                    write = True
                if _isinstance(mode, _str) and _any(c in mode for c in "wax+"):
                    write = True
                if write and not (POLICY == "tests" and inside(path)):
                    deny(event, "file write")
                if not write and not read_ok(path):
                    deny(event, "file read outside the sandbox")
            elif event in ("os.listdir", "os.scandir"):
                if not read_ok(args[0] if args else None):
                    deny(event, "directory listing outside the sandbox")
            elif event.startswith(_FS):
                paths = [a for a in args if _isinstance(a, (_str, _bytes, _PathLike))]
                if not (POLICY == "tests" and paths and _all(inside(p) for p in paths)):
                    deny(event, "file mutation")

        _sys.addaudithook(hook)

    _sys.dont_write_bytecode = True
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass
    _install()
    os.chdir(ROOT)
    for p in reversed(spec.get("sys_path", [])):
        _sys.path.insert(0, p)
    out = {"violations": _violations}
    real_out = _sys.stdout
    try:
        _sys.stdout = io.StringIO()
        if spec["mode"] == "call":
            import importlib
            mod = importlib.import_module(spec["module"])
            fn = getattr(mod, spec["func"])
            import ast
            args = ast.literal_eval(spec["args_repr"])
            results = []
            for _ in range(2):  # determinism check
                try:
                    results.append(("ok", repr(fn(*args))))
                except PermissionError:
                    raise
                except BaseException as e:
                    results.append(("raises", type(e).__name__))
            out["results"] = results
        elif spec["mode"] == "attrs":
            import importlib
            mod = importlib.import_module(spec["module"])
            vals = {n: [repr(getattr(mod, n, "<missing>"))] for n in spec["names"]}
            importlib.reload(mod)  # second evaluation: constants must be stable
            for n in spec["names"]:
                vals[n].append(repr(getattr(mod, n, "<missing>")))
            out["values"] = vals
        elif spec["mode"] == "exec":
            exec(compile(spec["code"], "<sandbox>", "exec"), {"__name__": "__sandbox__"})
        out["stdout"] = _sys.stdout.getvalue()[:10000]
    except BaseException as e:
        out["error"] = type(e).__name__ + ": " + str(e)[:500]
    finally:
        _sys.stdout = real_out
    _sys.stdout.write("\x00CDEBT-RESULT\x00" + json.dumps(out))
    _sys.stdout.flush()


_run()
"""


_NODE_RUNNER = r"""
import { pathToFileURL } from 'node:url';
import net from 'node:net';
import dns from 'node:dns';
import http from 'node:http';
import https from 'node:https';
import http2 from 'node:http2';
import tls from 'node:tls';
import dgram from 'node:dgram';
import inspector from 'node:inspector';
import v8 from 'node:v8';
const MARK = "\u0000CDEBT-RESULT\u0000";
const violations = [];
function blocked(name) {
  return function () { violations.push(name); throw new Error('cdebt sandbox blocked ' + name); };
}
// The permission model blocks fs writes, child processes and workers; the network it does not
// cover, so the well-known network entry points are disabled here (the OS layer and Docker's
// --network none are the real boundary).
const NET = [[net, ['connect', 'createConnection', 'createServer']],
  [dns, ['lookup', 'resolve', 'resolve4', 'resolve6', 'resolveAny', 'reverse']],
  [http, ['request', 'get', 'createServer']], [https, ['request', 'get', 'createServer']],
  [http2, ['connect', 'createServer', 'createSecureServer']], [tls, ['connect', 'createServer']],
  [dgram, ['createSocket']]];
for (const [mod, names] of NET) for (const n of names) { try { mod[n] = blocked(n); } catch (e) {} }
// the permission model does not cover the debugger port, V8 flags, diagnostic reports or wasm memory
for (const k of Object.keys(inspector)) { try { if (typeof inspector[k] === 'function') inspector[k] = blocked('inspector.' + k); } catch (e) {} }
for (const k of ['setFlagsFromString', 'writeHeapSnapshot', 'setHeapSnapshotNearHeapLimit']) { try { v8[k] = blocked('v8.' + k); } catch (e) {} }
try { process.report.writeReport = blocked('process.report.writeReport'); } catch (e) {}
try { delete globalThis.WebAssembly; } catch (e) {}
for (const g of ['fetch', 'WebSocket', 'EventSource', 'XMLHttpRequest']) { try { delete globalThis[g]; } catch (e) {} }
for (const k of ['kill', 'abort', 'reallyExit', 'dlopen', 'binding', '_linkedBinding']) { try { process[k] = blocked('process.' + k); } catch (e) {} }
try { net.Socket.prototype.connect = blocked('net.Socket.connect'); } catch (e) {}
try { net.Server.prototype.listen = blocked('net.Server.listen'); } catch (e) {}
try { for (const n of Object.keys(dns.promises)) dns.promises[n] = blocked('dns.promises.' + n); } catch (e) {}
function lit(v, depth) {
  depth = depth || 0;
  if (depth > 6) throw new Error('too deep');
  if (v === undefined) return 'undefined';
  if (v === null) return 'null';
  switch (typeof v) {
    case 'number':
      if (Number.isNaN(v)) return 'NaN';
      if (v === Infinity) return 'Infinity';
      if (v === -Infinity) return '-Infinity';
      if (Object.is(v, -0)) return '-0';
      return String(v);
    case 'string': return JSON.stringify(v);
    case 'boolean': return String(v);
    case 'bigint': return v.toString() + 'n';
    case 'object': {
      if (Array.isArray(v)) return '[' + v.map((x) => lit(x, depth + 1)).join(', ') + ']';
      const proto = Object.getPrototypeOf(v);
      if (proto !== Object.prototype && proto !== null) throw new Error('unsupported object');
      return '{' + Object.keys(v).map((k) => JSON.stringify(k) + ': ' + lit(v[k], depth + 1)).join(', ') + '}';
    }
    default: throw new Error('unsupported ' + typeof v);
  }
}
const out = { violations };
const realWrite = process.stdout.write.bind(process.stdout);
try {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const spec = JSON.parse(Buffer.concat(chunks).toString());
  console.log = console.info = console.warn = console.error = console.debug = () => {};
  process.stdout.write = () => true;
  const url = pathToFileURL(spec.module).href;
  if (spec.mode === 'call') {
    const m = await import(url);
    const fn = m[spec.func] !== undefined ? m[spec.func] : (m.default && m.default[spec.func]);
    if (typeof fn !== 'function') throw new Error('not a function: ' + spec.func);
    const args = JSON.parse(spec.args, (k, v) => (v && typeof v === 'object' && v.$undef ? undefined : v));
    out.results = [];
    for (let i = 0; i < 2; i++) {  // determinism check
      try { out.results.push(['ok', lit(await Promise.resolve(fn(...args)))]); }
      catch (e) {
        if (violations.length) throw e;
        out.results.push(['raises', (e && e.constructor && e.constructor.name) || 'Error']);
      }
    }
  } else if (spec.mode === 'attrs') {
    const first = await import(url);
    const second = await import(url + '?second');
    out.values = {};
    for (const n of spec.names) {
      let a, b;
      try { a = lit(first[n]); b = lit(second[n]); } catch (e) { a = b = '<unsupported>'; }
      out.values[n] = [a, b];
    }
  }
} catch (e) {
  out.error = ((e && e.constructor && e.constructor.name) || 'Error') + ': ' + String((e && e.message) || e).slice(0, 300) + (e && e.resource ? ' [' + e.resource + ']' : '');
}
process.stdout.write = realWrite;
realWrite(MARK + JSON.stringify(out));
process.exit(0);
"""

_SBX_OK: bool | None = None


def _sandbox_exec_available() -> bool:
    global _SBX_OK
    if _SBX_OK is None:
        _SBX_OK = False
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            try:
                p = subprocess.run(
                    ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                    capture_output=True,
                    timeout=10,
                )
                _SBX_OK = p.returncode == 0
            except Exception:  # noqa: BLE001
                _SBX_OK = False
    return _SBX_OK


def _sbx_profile(
    root: str, *, strict: bool = False, allow_fork: bool = True, read_roots: list[str] | None = None
) -> str:
    """macOS Seatbelt profile. Always: no network, writes only under `root`. `strict` also hides
    the user's HOME (ssh keys, other repos, tokens) except `read_roots` (interpreter prefixes and
    the sandbox root) and, unless `allow_fork`, denies process creation at the OS level."""
    r = os.path.realpath(root)
    prof = (
        "(version 1)(allow default)(deny network*)(deny file-write*)"
        f'(allow file-write* (subpath "{r}") (literal "/dev/null") (literal "/dev/tty") '
        '(subpath "/dev/fd"))'
    )
    prof += "(deny signal)(allow signal (target self))(allow signal (target same-sandbox))"
    if strict:
        home = os.path.realpath(os.path.expanduser("~"))
        prof += f'(deny file-read* (subpath "{home}"))'
        prof += "(allow file-read-metadata)"  # path traversal needs stat() on every ancestor
        for x in [r, *(read_roots or [])]:
            prof += f'(allow file-read* (subpath "{os.path.realpath(x)}"))'
        if not allow_fork:
            prof += "(deny process-fork)"
    return prof


def _strict_read_roots(py: str, node_bin: str | None) -> list[str]:
    import sysconfig

    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, *sysconfig.get_paths().values()}
    roots.add(str(Path(os.path.realpath(py)).parent.parent))
    if node_bin:
        roots.add(str(Path(os.path.realpath(node_bin)).parent.parent))
    return sorted(roots)


Isolation = Literal["host", "strong", "docker"]
DEFAULT_ISOLATION: Isolation = "host"  # library default; the CLI sets "strong" unless --trusted


def set_default_isolation(mode: Isolation) -> None:
    """host: audit hook/permission model + rlimits (+ Seatbelt on macOS). strong: Docker if
    available, else strict Seatbelt (macOS), else REFUSE to run. docker: Docker or refuse."""
    global DEFAULT_ISOLATION
    DEFAULT_ISOLATION = mode


def is_trusted(flag: bool = False) -> bool:
    """A repo is trusted only by an explicit choice of the person running cdebt: `--trusted` or
    CDEBT_TRUSTED=1. NEVER from a file inside the repo (a PR author controls those)."""
    return flag or os.environ.get("CDEBT_TRUSTED") == "1"


def _capped_communicate(
    proc: subprocess.Popen[bytes],
    stdin_bytes: bytes | None,
    wait_timeout: float,
    on_exceed: Callable[[], None],
) -> tuple[bytes, bytes]:
    """Like Popen.communicate but keeps at most MAX_OUTPUT / MAX_STDERR bytes and calls
    `on_exceed` (kill) the moment a stream goes over, so a flood cannot exhaust our memory."""
    bufs = {"out": bytearray(), "err": bytearray()}
    caps = {"out": MAX_OUTPUT, "err": MAX_STDERR}
    tripped = threading.Event()

    def reader(stream: Any, key: str) -> None:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                return
            room = caps[key] - len(bufs[key])
            if room > 0:
                bufs[key].extend(chunk[:room])
            if len(chunk) > room and not tripped.is_set():
                tripped.set()
                on_exceed()

    def writer() -> None:
        with contextlib.suppress(BrokenPipeError, OSError):
            if stdin_bytes and proc.stdin:
                proc.stdin.write(stdin_bytes)
        with contextlib.suppress(OSError):
            if proc.stdin:
                proc.stdin.close()

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, "out"), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, "err"), daemon=True),
        threading.Thread(target=writer, daemon=True),
    ]
    for t in threads:
        t.start()
    proc.wait(timeout=wait_timeout)  # raises TimeoutExpired like communicate(timeout=...)
    for t in threads:
        t.join(timeout=3)
    return bytes(bufs["out"]), bytes(bufs["err"])


MAX_OUTPUT = 8 << 20  # bytes of stdout kept; more kills the run
MAX_STDERR = 1 << 20
MAX_PROCS = 64  # descendants allowed in "tests"/command mode
DOCKER_IMAGE = "python:3.11-slim"
NODE_IMAGE = "node:24-slim"
NODE_RUNNER_NAME = ".cdebt_runner.mjs"
_DOCKER_IMAGES: dict[str, bool] = {}


WINDOWS_MESSAGE = (
    "Windows is not supported for commands that execute code (quiz, attest, coverage, study run): "
    "the sandbox needs POSIX process groups, rlimits and signals. Use WSL2 or Linux/macOS. "
    "map, gate, plan, backtest and study analyze work on Windows."
)


def require_posix() -> None:
    if sys.platform == "win32":
        raise SandboxUnavailable(WINDOWS_MESSAGE)


class SandboxUnavailable(RuntimeError):
    """The requested runtime/isolation is not available on this machine."""


def docker_available(image: str = DOCKER_IMAGE) -> bool:
    """Needs a running Docker daemon and the image already pulled (never pulls by itself)."""
    if image not in _DOCKER_IMAGES:
        ok = False
        if shutil.which("docker") and os.environ.get("CDEBT_NO_DOCKER") != "1":
            try:
                p = subprocess.run(
                    ["docker", "image", "inspect", image], capture_output=True, timeout=15
                )
                ok = p.returncode == 0
            except Exception:  # noqa: BLE001
                ok = False
        _DOCKER_IMAGES[image] = ok
    return _DOCKER_IMAGES[image]


def _rss_kb(pid: int) -> int:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        return int(out) if out else 0
    except Exception:  # noqa: BLE001
        return 0


def _kill_group(pgid: int) -> None:
    """SIGKILL a process group whose id is `pgid` (the sandbox child is a session leader, so
    pgid == its pid). Refuses if that pid now belongs to an unrelated process (PID reuse after
    the leader was reaped)."""
    if sys.platform == "win32":
        return
    try:
        cur: int | None = os.getpgid(pgid)
    except ProcessLookupError:
        cur = None  # leader gone: remaining members (if any) still carry this group id
    except OSError:
        return
    if cur not in (None, pgid):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, signal.SIGKILL)


def _descendants(pid: int) -> list[int]:
    """All live descendants of `pid` (so children that left the process group by calling
    setsid are killed too). Best effort: needs `ps`."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    kids: dict[int, list[int]] = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    stack = [pid]
    while stack:
        for k in kids.get(stack.pop(), []):
            found.append(k)
            stack.append(k)
    return found


def _kill_tree(pid: int) -> None:
    """Kill the whole process tree: descendants first (they may have escaped the group), then
    the group, then the group again (a fork racing the first pass)."""
    if sys.platform == "win32":
        return
    for d in _descendants(pid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(d, signal.SIGKILL)
    _kill_group(pid)
    _kill_group(pid)


def _docker(args: list[str], timeout: float) -> None:
    """Run a docker CLI command with a hard timeout; never raises."""
    with contextlib.suppress(Exception):
        subprocess.run(["docker", *args], capture_output=True, timeout=timeout)


_REAPED = False


def _reap_orphan_containers() -> None:
    """Remove `cdebt-<pid>-...` containers whose creating process is gone (it was killed
    between `docker create` and cleanup). Once per process."""
    global _REAPED
    if _REAPED:
        return
    _REAPED = True
    try:
        names = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=cdebt-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.split()
    except Exception:  # noqa: BLE001
        return
    for n in names:
        parts = n.split("-")
        if len(parts) >= 3 and parts[1].isdigit():
            try:
                os.kill(int(parts[1]), 0)
            except ProcessLookupError:
                _docker(["rm", "-f", n], 30)
            except PermissionError:
                pass


def sanitize_root(root: Path) -> list[str]:
    """Remove, from the sandbox root, symlinks that resolve outside it and regular files with more
    than one hard link (which can alias a file outside the root). Neither can be told apart from a
    legitimate file by path-based guards (Node's permission model follows such symlinks). The
    root is a temporary copy we created, so unlinking is safe. Returns what was removed."""
    removed: list[str] = []
    base = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = os.path.join(dirpath, name)
            try:
                if os.path.islink(p):
                    tgt = os.path.realpath(p)
                    if not (tgt == base or tgt.startswith(base + os.sep)):
                        os.unlink(p)
                        removed.append(os.path.relpath(p, root))
                elif os.path.isfile(p) and os.lstat(p).st_nlink > 1:
                    os.unlink(p)
                    removed.append(os.path.relpath(p, root))
            except OSError:
                continue
    return removed


def run_in_sandbox(
    spec: dict[str, object],
    *,
    root: Path,
    timeout: float = 10.0,
    mem_mb: int = 512,
    policy: Policy = "pure",
    python: str | None = None,
    command: list[str] | None = None,
    use_os_isolation: bool = True,
    extra_env: dict[str, str] | None = None,
    runtime: Literal["python", "node"] = "python",
    isolation: Isolation | None = None,
    sanitize: bool = True,
) -> SandboxResult:
    """Run the runner (mode call/exec; `runtime` selects the Python or Node runner) or, with
    `command`, an arbitrary command (policy "tests") under the same containment. `root` is the
    only writable directory."""
    if sys.platform == "win32":  # explicit so type checkers treat the rest as POSIX-only
        raise SandboxUnavailable(WINDOWS_MESSAGE)
    orig_root = str(root)
    root = Path(root).resolve()
    if sanitize:
        sanitize_root(root)
    (root / "tmp").mkdir(exist_ok=True)
    runner = Path(tempfile.mkdtemp(prefix="cdebt-runner-")) / "runner.py"
    runner.write_text(_RUNNER)
    spec = {**spec, "root": str(root), "policy": policy}
    py = python or sys.executable
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root),
        "TMPDIR": str(root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
        "NO_PROXY": "*",
        "PYTHONUNBUFFERED": "1",
    }
    env.update(extra_env or {})
    iso_label = "audit+rlimit"
    node_flags = [
        "--permission",  # Node's permission model: no fs writes, no child processes, no workers
        f"--allow-fs-read={root}",
        f"--max-old-space-size={max(64, mem_mb // 2)}",
    ]
    if runtime == "node":
        node_bin = shutil.which("node")
        if node_bin is None:
            raise SandboxUnavailable("node is not installed")
        (root / NODE_RUNNER_NAME).write_text(_NODE_RUNNER)
        argv = [node_bin, *node_flags, str(root / NODE_RUNNER_NAME)]
        stdin_data = json.dumps(spec)
        runner_src = ""
    elif command is not None:
        argv = list(command)
        stdin_data = None
        runner_src = ""
    else:
        argv = [py, "-I", str(runner)]
        stdin_data = json.dumps(spec)
        runner_src = runner.read_text()
    docker_name: str | None = None
    image = NODE_IMAGE if runtime == "node" else DOCKER_IMAGE
    mode: Isolation = isolation or DEFAULT_ISOLATION
    strict_seatbelt = False
    if use_os_isolation and mode in ("strong", "docker"):
        docker_ok = command is None and docker_available(image)
        if mode == "docker" and not docker_ok:
            raise SandboxUnavailable(
                f"Docker isolation requested but unavailable (needs a running Docker daemon with "
                f"image {image} pulled, and cannot run a project's test suite)"
            )
        if mode == "strong" and not docker_ok:
            if _sandbox_exec_available():
                strict_seatbelt = True
            else:
                raise SandboxUnavailable(
                    "this repository is not marked trusted, so probes need strong isolation: "
                    f"Docker (with {image} pulled) or macOS sandbox-exec. Neither is available. "
                    "Install/start Docker, or pass --trusted (or set CDEBT_TRUSTED=1) if you trust "
                    "this repository's code."
                )
    want_docker = (
        use_os_isolation
        and command is None
        and mode in ("strong", "docker")
        and docker_available(image)
    )
    if want_docker:
        # Files are copied in with `docker cp` (no bind mounts: temp dirs are usually not
        # shared with the Docker VM). No network, capped memory/pids, no capabilities,
        # unprivileged user. The audit hook still runs inside the container.
        iso_label = "docker"
        _reap_orphan_containers()
        docker_name = f"cdebt-{os.getpid()}-{int(time.time() * 1e6)}"
        stdin_data = json.dumps(spec).replace(str(root), "/work").replace(orig_root, "/work")
        created = subprocess.run(
            [
                "docker",
                "create",
                "--name",
                docker_name,
                "-i",
                "--network",
                "none",
                "--memory",
                f"{mem_mb}m",
                "--memory-swap",
                f"{mem_mb}m",
                "--pids-limit",
                "64",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "65534:65534",
                "--tmpfs",
                "/tmp:rw,size=64m",
                "-w",
                "/work",
                "-e",
                "HOME=/tmp",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                "PYTHONHASHSEED=0",
                image,
                *(
                    [
                        "node",
                        "--permission",
                        "--allow-fs-read=/work",
                        f"--max-old-space-size={max(64, mem_mb // 2)}",
                        f"/work/{NODE_RUNNER_NAME}",
                    ]
                    if runtime == "node"
                    else ["python", "-I", "-c", runner_src]
                ),
            ],
            capture_output=True,
            timeout=60,
        )
        subprocess.run(["chmod", "-R", "a+rX", str(root)], capture_output=True)  # nobody must read
        copied = (
            created.returncode == 0
            and subprocess.run(
                ["docker", "cp", f"{root}/.", f"{docker_name}:/work"],
                capture_output=True,
                timeout=120,
            ).returncode
            == 0
        )
        if not copied:
            _docker(["rm", "-f", docker_name], 30)
            docker_name, iso_label = None, "audit+rlimit"
        else:
            docker_bin = shutil.which("docker") or "docker"
            env["PATH"] = f"{Path(docker_bin).parent}:{env['PATH']}"
            env["HOME"] = os.environ.get("HOME", env["HOME"])  # docker CLI context lives there
            argv = [docker_bin, "start", "-a", "-i", docker_name]
    if docker_name is None and use_os_isolation and _sandbox_exec_available():
        prof = _sbx_profile(
            str(root),
            strict=strict_seatbelt,
            allow_fork=command is not None,  # test suites fork; probe runners never need to
            read_roots=_strict_read_roots(py, shutil.which("node")) if strict_seatbelt else None,
        )
        argv = ["sandbox-exec", "-p", prof, *argv]
        iso_label += "+sandbox-exec" + ("(strict)" if strict_seatbelt else "")

    client_only = docker_name is not None  # the docker CLI is only a client; limits live in the
    # container (a Go binary also cannot run under RLIMIT_AS: "failed to reserve page summary")

    def preexec() -> None:
        os.setsid()
        if client_only:
            return
        for name, val in (
            ("RLIMIT_CPU", int(timeout) + 2),
            ("RLIMIT_NOFILE", 256),
            ("RLIMIT_CORE", 0),
        ) + ((("RLIMIT_FSIZE", 0 if command is None else 512 << 20),) if policy else ()):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(getattr(resource, name), (val, val))
        if sys.platform != "darwin" and runtime != "node":  # not enforced on macOS; breaks V8
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(resource.RLIMIT_AS, (mem_mb << 20, mem_mb << 20))

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=preexec,
        )
    except Exception:
        if docker_name:
            _docker(["rm", "-f", docker_name], 30)
        raise
    flags = {"mem": False, "timeout": False, "output": False, "procs": False}
    done = threading.Event()

    def terminate(reason: str) -> None:
        if proc.poll() is not None:  # already exited by itself: not a timeout/mem kill
            return
        flags[reason] = True
        if docker_name:  # the CONTAINER is what runs the code: kill it, not just the client
            _docker(["kill", docker_name], 10)
        _kill_tree(proc.pid)

    # The wall-clock limit is its own timer: it must not depend on how long `ps` (the RSS poll
    # below) takes under load. (Previously one slow `ps` delayed the timeout check.)
    timer = threading.Timer(timeout, terminate, args=("timeout",))
    timer.daemon = True
    timer.start()

    def rss_watch() -> None:
        while not done.wait(0.25):
            if docker_name is None and _rss_kb(proc.pid) > mem_mb * 1024:
                terminate("mem")
                return
            if command is not None and len(_descendants(proc.pid)) > MAX_PROCS:
                terminate("procs")  # fork bomb in a test suite (probe runners cannot fork)
                return

    threading.Thread(target=rss_watch, daemon=True).start()
    try:
        out, err = _capped_communicate(
            proc,
            stdin_data.encode() if stdin_data else None,
            timeout + 5,
            lambda: terminate("output"),
        )
    except subprocess.TimeoutExpired:
        terminate("timeout")  # backstop if the timer thread could not run
        _kill_tree(proc.pid)
        out, err = b"", b""
        flags["timeout"] = True
    t_end = time.monotonic()  # time until the code stopped running, before any cleanup
    done.set()
    timer.cancel()
    try:
        _kill_tree(proc.pid)  # reap stragglers in the tree
        if docker_name:
            _docker(["rm", "-f", docker_name], 30)
    finally:
        shutil.rmtree(runner.parent, ignore_errors=True)
    text = out.decode("utf-8", "replace")
    payload: dict[str, object] | None = None
    if MARK in text:
        text, _, tail = text.rpartition(MARK)
        try:
            loaded = json.loads(tail)
            payload = loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            payload = None
    raw_viol = (payload or {}).get("violations", [])
    viol = [str(v) for v in raw_viol] if isinstance(raw_viol, list) else []
    return SandboxResult(
        proc.returncode,
        text,
        err.decode("utf-8", "replace")[-4000:],
        timed_out=flags["timeout"],
        killed_for_memory=flags["mem"],
        killed_for_output=flags["output"],
        killed_for_procs=flags["procs"],
        violations=viol,
        isolation=iso_label,
        payload=payload,
        elapsed=t_end - t0,
    )


def run_code(
    code: str,
    *,
    timeout: float = 5.0,
    mem_mb: int = 256,
    os_isolation: bool = True,
    isolation: Isolation | None = None,
) -> SandboxResult:
    """Run arbitrary Python text under the 'pure' policy in a fresh temp dir (used by tests
    and by probes). Cleans up its directory."""
    d = Path(tempfile.mkdtemp(prefix="cdebt-sbx-"))
    try:
        return run_in_sandbox(
            {"mode": "exec", "code": code},
            root=d,
            timeout=timeout,
            mem_mb=mem_mb,
            use_os_isolation=os_isolation,
            isolation=isolation,
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)
