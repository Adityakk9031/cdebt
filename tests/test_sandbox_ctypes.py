"""Red-team tests for importing ctypes under the OS layer. `import ctypes` runs `dlopen(None)`
(numpy, pandas and scipy import ctypes). With the OS layer on (strict Seatbelt or Docker) the runner
imports ctypes BEFORE the guard and strips the helpers that touch raw memory without an audit
event (memmove, memset, string_at, cast, CFUNCTYPE, the `_ctypes` address constants); the guard
still blocks every other ctypes event. Without the OS layer ctypes stays unimportable.
Each attack prints PWNED if it works; none may."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cdebt.sandbox import _sandbox_exec_available, docker_available, run_code

pytestmark = [
    pytest.mark.posix_sandbox,
    pytest.mark.skipif(
        not (_sandbox_exec_available() or docker_available()),
        reason="needs strict Seatbelt (macOS) or Docker",
    ),
]


def strong(code: str):  # type: ignore[no-untyped-def]
    return run_code(code, isolation="strong", timeout=20)


def blocked(res) -> bool:  # type: ignore[no-untyped-def]
    pl = res.payload or {}
    stopped = bool(res.violations) or "error" in pl  # refused by the guard, or nothing to use
    return stopped and "PWNED" not in str(pl.get("stdout", ""))


def test_importing_ctypes_works_with_the_os_layer() -> None:
    res = strong("import ctypes\nprint('imported', ctypes.sizeof(ctypes.c_int))")
    assert res.violations == [] and "imported 4" in str((res.payload or {}).get("stdout"))
    assert "strict" in res.isolation or res.isolation == "docker"


def test_importing_ctypes_is_still_blocked_without_the_os_layer() -> None:
    res = run_code("import ctypes", os_isolation=False)
    assert any("ctypes.dlopen" in v for v in res.violations)
    res = run_code("import ctypes")  # default isolation "host": audit hook only
    assert any("ctypes.dlopen" in v for v in res.violations)


ATTACKS = {
    "second dlopen": "import ctypes\nctypes.CDLL(None)\nprint('PWNED')",
    "libc system": (
        "import ctypes\nctypes.CDLL(None).system(b'touch /tmp/cdebt_pwn_1')\nprint('PWNED')"
    ),
    "pythonapi": (
        "import ctypes\nctypes.pythonapi.PyRun_SimpleString(b'print(1)')\nprint('PWNED')"
    ),
    "string_at": "import ctypes\nprint(ctypes.string_at(0, 1), 'PWNED')",
    "memmove": "import ctypes\nb = bytearray(4)\nctypes.memmove(b, b, 0)\nprint('PWNED')",
    "cast": "import ctypes\nctypes.cast(ctypes.c_void_p(0), ctypes.c_char_p)\nprint('PWNED')",
    "function pointer": (
        "import ctypes\nf = ctypes.CFUNCTYPE(ctypes.c_int)(0)\nf()\nprint('PWNED')"
    ),
    "raw _ctypes after use": ("import ctypes, _ctypes\nh = _ctypes.dlopen(None)\nprint('PWNED')"),
    "raw _ctypes before": "import _ctypes\nh = _ctypes.dlopen(None)\nprint('PWNED')",
    "dlsym on a stolen handle": (
        "import ctypes, _ctypes\nh = ctypes.pythonapi._handle\n_ctypes.dlsym(h, 'system')\n"
        "print('PWNED')"
    ),
    "memmove with ints": "import ctypes\nctypes.memmove(0, 0, 0)\nprint('PWNED')",
    "memset with ints": "import ctypes\nctypes.memset(0, 0, 0)\nprint('PWNED')",
    "string_at address constant": ("import _ctypes\nprint(_ctypes._string_at_addr, 'PWNED')"),
    "memmove address constant": (
        "import ctypes, _ctypes\nf = ctypes.CFUNCTYPE(ctypes.c_int)(_ctypes._memmove_addr)\n"
        "print('PWNED')"
    ),
    "load_library": (
        "import ctypes.util, ctypes\nctypes.cdll.LoadLibrary('libc.dylib')\nprint('PWNED')"
    ),
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_everything_but_the_init_dlopen_stays_blocked(name: str) -> None:
    res = strong(ATTACKS[name])
    assert blocked(res), (name, res.violations, res.payload)
    assert not Path("/tmp/cdebt_pwn_1").exists()


def test_reloading_ctypes_does_not_redo_the_dlopen() -> None:
    res = strong("import ctypes, importlib\nimportlib.reload(ctypes)\nprint('PWNED')")
    assert blocked(res), (res.violations, res.payload)


def test_writes_and_network_stay_blocked_after_importing_ctypes() -> None:
    for body in (
        "open('/tmp/cdebt_pwn_2', 'w').write('x')",
        "import socket; socket.socket().connect(('127.0.0.1', 9))",
        "import os; os.system('true')",
    ):
        res = strong(f"import ctypes\n{body}\nprint('PWNED')")
        assert blocked(res), (body, res.violations)


@pytest.mark.skipif(importlib.util.find_spec("numpy") is None, reason="numpy not installed")
def test_numpy_runs_under_the_os_layer_and_not_without_it() -> None:
    code = "import numpy as np\nprint('sum', int(np.arange(5).sum()))"
    ok = strong(code)
    assert ok.violations == [] and "sum 10" in str((ok.payload or {}).get("stdout")), ok.payload
    assert run_code(code, os_isolation=False).violations  # audit-only mode still refuses it
