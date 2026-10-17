"""Sandbox escape tests: each hostile payload must be CONTAINED and reported."""

from __future__ import annotations

import socket
import threading
from pathlib import Path

from cdebt.sandbox import run_code, screen_source


def test_file_write_contained(tmp_path: Path) -> None:
    target = tmp_path / "pwned.txt"
    r = run_code(f"open({str(target)!r}, 'w').write('x')")
    assert not target.exists() and r.violations


def test_file_write_via_os_open_and_remove(tmp_path: Path) -> None:
    f = tmp_path / "victim.txt"
    f.write_text("keep")
    code = (
        f"import os\nfor fn in (lambda: os.remove({str(f)!r}),"
        f" lambda: os.open({str(tmp_path / 'n.txt')!r}, os.O_CREAT | os.O_WRONLY)):\n"
        "    try: fn()\n    except PermissionError: pass\n"
    )
    r = run_code(code)
    assert f.read_text() == "keep" and not (tmp_path / "n.txt").exists() and len(r.violations) >= 2


def test_network_contained() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    hits: list[int] = []

    def accept() -> None:
        try:
            srv.accept()
            hits.append(1)
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()
    r = run_code(f"import socket\ns = socket.socket()\ns.connect(('127.0.0.1', {port}))")
    srv.close()
    assert not hits and r.violations


def test_dns_contained() -> None:
    r = run_code("import socket\nsocket.getaddrinfo('example.com', 80)")
    assert r.violations


def test_fork_bomb_contained() -> None:
    code = "import os\nwhile True:\n    os.fork()\n"
    r = run_code(code, timeout=5)
    assert r.violations and not r.timed_out  # first fork is blocked, no children exist


def test_subprocess_contained(tmp_path: Path) -> None:
    marker = tmp_path / "m"
    r = run_code(f"import subprocess\nsubprocess.run(['touch', {str(marker)!r}])")
    assert not marker.exists() and r.violations


def test_infinite_loop_killed() -> None:
    r = run_code("while True:\n    pass\n", timeout=1.5)
    assert r.timed_out and r.elapsed < 6


def test_memory_bomb_killed() -> None:
    r = run_code(
        "x = []\nwhile True:\n    x.append(bytearray(50_000_000))\n", timeout=20, mem_mb=200
    )
    # Contained one of three ways depending on the OS: watchdog kill (macOS: RLIMIT_AS is not
    # enforced), non-zero exit, or RLIMIT_AS turning the allocation into a MemoryError that the
    # runner reports (Linux).
    err = str((r.payload or {}).get("error", ""))
    assert r.killed_for_memory or r.returncode != 0 or err.startswith("MemoryError")
    assert r.elapsed < 20


def test_ctypes_contained() -> None:
    r = run_code("import ctypes\nctypes.CDLL(None)")
    assert r.violations


def test_exec_import_trick_contained(tmp_path: Path) -> None:
    t = tmp_path / "t.txt"
    r = run_code(f"__import__('builtins').open({str(t)!r}, 'w')")
    assert not t.exists() and r.violations


def test_screen_flags_side_effects() -> None:
    assert screen_source("import os\n")
    assert screen_source("def f():\n    return open('x').read()\n")
    assert screen_source("import random\n")
    assert screen_source("from time import sleep\n")
    assert screen_source("x = ")  # syntax error
    assert screen_source("def f(a: int) -> int:\n    return a + 1\n") == []


def test_clean_code_runs() -> None:
    r = run_code("print('hi')")
    assert r.returncode == 0 and r.payload is not None and r.payload["stdout"] == "hi\n"
    assert not r.violations


def test_audit_layer_alone_contains_everything(tmp_path: Path) -> None:
    """Without OS-level isolation (no sandbox-exec/docker) the audit hook still holds."""
    f = tmp_path / "w.txt"
    r = run_code(f"open({str(f)!r}, 'w')", os_isolation=False)
    assert r.isolation == "audit+rlimit" and not f.exists() and r.violations
    assert run_code(
        "import socket\nsocket.socket().connect(('127.0.0.1', 9))", os_isolation=False
    ).violations
    assert run_code("import os\nos.fork()", os_isolation=False).violations


def test_os_isolation_label_reported() -> None:
    r = run_code("pass")
    assert r.isolation.startswith(("audit+rlimit", "docker"))
