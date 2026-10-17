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
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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


@dataclass
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    killed_for_memory: bool = False
    violations: list[str] = field(default_factory=list)
    isolation: str = "audit+rlimit"
    payload: dict[str, object] | None = None  # JSON the runner emitted, if any
    elapsed: float = 0.0


_RUNNER = r"""
import sys, os, json, io, resource
spec = json.loads(sys.stdin.read())
ROOT = os.path.realpath(spec["root"]); POLICY = spec["policy"]
violations = []
_BLOCK = ("socket.", "ctypes.", "winreg.")
_PROC = ("subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.exec", "os.posix_spawn",
         "os.spawn", "os.startfile", "pty.spawn")
_FS = ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.truncate", "os.symlink", "os.link",
       "os.chmod", "os.chown", "os.utime", "os.mkfifo", "os.mknod", "shutil.", "os.removexattr",
       "os.setxattr")
def _inside(p):
    try:
        rp = os.path.realpath(p if isinstance(p, (str, bytes, os.PathLike)) else str(p))
    except Exception:
        return False
    rp = os.fsdecode(rp)
    return rp == ROOT or rp.startswith(ROOT + os.sep) or rp in ("/dev/null", "/dev/tty")
def _deny(ev, why):
    violations.append(ev + ": " + why)
    raise PermissionError("cdebt sandbox blocked " + ev + " (" + why + ")")
def hook(event, args):
    if event.startswith(_BLOCK):
        _deny(event, "network/native")
    if event.startswith(_PROC) and POLICY == "pure":
        _deny(event, "process creation")
    if event == "open":
        path, mode, flags = args
        write = False
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            write = True
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            write = True
        if write and not (POLICY == "tests" and _inside(path)):
            _deny(event, "file write")
    elif event.startswith(_FS):
        paths = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
        if not (POLICY == "tests" and paths and all(_inside(p) for p in paths)):
            _deny(event, "file mutation")
sys.dont_write_bytecode = True
def _limits():
    for name, val in (("RLIMIT_CORE", 0),):
        try: resource.setrlimit(getattr(resource, name), (val, val))
        except Exception: pass
_limits()
sys.addaudithook(hook)
os.chdir(ROOT)
for p in reversed(spec.get("sys_path", [])):
    sys.path.insert(0, p)
out = {"violations": violations}
real_out = sys.stdout
try:
    sys.stdout = io.StringIO()
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
    elif spec["mode"] == "exec":
        exec(compile(spec["code"], "<sandbox>", "exec"), {"__name__": "__sandbox__"})
    out["stdout"] = sys.stdout.getvalue()[:10000]
except BaseException as e:
    out["error"] = type(e).__name__ + ": " + str(e)[:500]
finally:
    sys.stdout = real_out
sys.stdout.write("\x00CDEBT-RESULT\x00" + json.dumps(out))
sys.stdout.flush()
"""

_SBX_OK: bool | None = None
_DOCKER_OK: bool | None = None


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


def _sbx_profile(root: str) -> str:
    r = os.path.realpath(root)
    return (
        "(version 1)(allow default)(deny network*)(deny file-write*)"
        f'(allow file-write* (subpath "{r}") (literal "/dev/null") (literal "/dev/tty") '
        '(subpath "/dev/fd"))'
    )


DOCKER_IMAGE = "python:3.11-slim"


def docker_available(image: str = DOCKER_IMAGE) -> bool:
    """Needs a running Docker daemon and the image already pulled (never pulls by itself)."""
    global _DOCKER_OK
    if _DOCKER_OK is None:
        _DOCKER_OK = False
        if shutil.which("docker") and os.environ.get("CDEBT_NO_DOCKER") != "1":
            try:
                p = subprocess.run(
                    ["docker", "image", "inspect", image], capture_output=True, timeout=15
                )
                _DOCKER_OK = p.returncode == 0
            except Exception:  # noqa: BLE001
                _DOCKER_OK = False
    return _DOCKER_OK


def _rss_kb(pid: int) -> int:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        return int(out) if out else 0
    except Exception:  # noqa: BLE001
        return 0


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


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
) -> SandboxResult:
    """Run the runner (mode call/exec) or, with `command`, an arbitrary command (policy
    "tests") under the same containment. `root` is the only writable directory."""
    orig_root = str(root)
    root = Path(root).resolve()
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
    isolation = "audit+rlimit"
    if command is not None:
        argv = list(command)
        stdin_data = None
    else:
        argv = [py, "-I", str(runner)]
        stdin_data = json.dumps(spec)
    docker_name: str | None = None
    if use_os_isolation and command is None and docker_available():
        # Files are copied in with `docker cp` (no bind mounts: temp dirs are usually not
        # shared with the Docker VM). No network, capped memory/pids, no capabilities,
        # unprivileged user. The audit hook still runs inside the container.
        isolation = "docker"
        docker_name = f"cdebt-{os.getpid()}-{int(time.time() * 1e6)}"
        runner_src = runner.read_text()
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
                DOCKER_IMAGE,
                "python",
                "-I",
                "-c",
                runner_src,
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
            subprocess.run(["docker", "rm", "-f", docker_name], capture_output=True)
            docker_name, isolation = None, "audit+rlimit"
        else:
            docker_bin = shutil.which("docker") or "docker"
            env["PATH"] = f"{Path(docker_bin).parent}:{env['PATH']}"
            env["HOME"] = os.environ.get("HOME", env["HOME"])  # docker CLI context lives there
            argv = [docker_bin, "start", "-a", "-i", docker_name]
    if docker_name is None and use_os_isolation and _sandbox_exec_available():
        argv = ["sandbox-exec", "-p", _sbx_profile(str(root)), *argv]
        isolation += "+sandbox-exec"

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
        ) + ((("RLIMIT_FSIZE", 0),) if policy == "pure" and command is None else ()):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(getattr(resource, name), (val, val))
        if sys.platform != "darwin":  # RLIMIT_AS is not enforced on macOS
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
            subprocess.run(["docker", "rm", "-f", docker_name], capture_output=True)
        raise
    flags = {"mem": False, "timeout": False}
    done = threading.Event()

    def watchdog() -> None:
        while not done.wait(0.05):
            if time.monotonic() - t0 > timeout:
                flags["timeout"] = True
                _kill_group(proc.pid)
                return
            if docker_name is None and _rss_kb(proc.pid) > mem_mb * 1024:
                flags["mem"] = True
                _kill_group(proc.pid)
                return

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()
    try:
        out, err = proc.communicate(
            stdin_data.encode() if stdin_data else None, timeout=timeout + 5
        )
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        out, err = proc.communicate()
        flags["timeout"] = True
    finally:
        done.set()
        _kill_group(proc.pid)  # reap any stragglers in the process group
        if docker_name:
            subprocess.run(["docker", "rm", "-f", docker_name], capture_output=True)
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
    viol = [str(v) for v in (payload or {}).get("violations", [])]  # type: ignore[attr-defined]
    return SandboxResult(
        proc.returncode,
        text,
        err.decode("utf-8", "replace")[-4000:],
        timed_out=flags["timeout"],
        killed_for_memory=flags["mem"],
        violations=viol,
        isolation=isolation,
        payload=payload,
        elapsed=time.monotonic() - t0,
    )


def run_code(
    code: str, *, timeout: float = 5.0, mem_mb: int = 256, os_isolation: bool = True
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
        )
    finally:
        shutil.rmtree(d, ignore_errors=True)
