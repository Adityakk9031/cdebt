"""Second adversarial pass (red team). Explicit payload classes the first fuzz suite did not have:
tampering with the in-process guard, shared memory, /proc and /sys reads, credential files,
unix sockets, signal tricks, archive bombs, planted symlinks and hardlinks, repo symlinks that
point at host secrets, the Node debugger port. Each payload runs in a disposable worker process and
must leave the world untouched (see test_sandbox_fuzz.World). Several of these FAILED against the
first version of the sandbox; see docs/THREAT_MODEL.md section 5."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from cdebt import sandbox
from cdebt.probes.base import copy_tree
from cdebt.probes.js import node_ok
from conftest import Repo
from test_sandbox_fuzz import FILE_SECRET, TIMEOUT, World, _modes, run, run_in_root


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CDEBT_CANARY_SECRET", "ENV-CANARY-77be02")
    w = World(tmp_path)
    yield w
    w.close()


def _each_mode(world: World, code: str, what: str) -> None:
    for mode in _modes():
        res = run(code, mode)
        world.check(res, f"[{what}/{mode}] {code!r}")


def _attack(world: World, body: str) -> str:
    """After the preamble `body` (a guard-tampering attempt), try every forbidden thing."""
    w = world
    return (
        f"{body}\n"
        f"for _a in (lambda: open({str(w.victim)!r}, 'w').write('pwn'),\n"
        f"           lambda: print(open({str(w.canary)!r}).read()),\n"
        f"           lambda: __import__('socket').create_connection(('127.0.0.1', {w.tcp_port})),\n"
        f"           lambda: __import__('os').remove({str(w.victim)!r}),\n"
        f"           lambda: __import__('os').kill({w.sentinel.pid}, 9)):\n"
        f"    try:\n        _a()\n    except BaseException:\n        pass\n"
    )


TAMPER = {
    "main_hook_code_swap": "import __main__\nfor n in dir(__main__):\n    f = getattr(__main__, n)\n    if callable(f) and hasattr(f, '__code__'):\n        try:\n            f.__code__ = (lambda *a, **k: None).__code__\n        except Exception:\n            pass",
    "gc_find_hook": "import gc\nfor o in gc.get_objects():\n    if getattr(o, '__name__', '') in ('hook', 'deny', 'read_ok', 'inside', 'realpath'):\n        o.__code__ = (lambda *a, **k: True).__code__",
    "gc_referrers": "import gc, sys\nfor r in gc.get_referrers(sys.modules):\n    pass",
    "traceback_frame_walk": "try:\n    1 / 0\nexcept Exception as e:\n    f = e.__traceback__.tb_frame\n    while f.f_back:\n        f = f.f_back\n    for k, v in list(f.f_locals.items()):\n        if callable(v) and hasattr(v, '__code__'):\n            try:\n                v.__code__ = (lambda *a, **k: True).__code__\n            except Exception:\n                pass",
    "sys_getframe_walk": "import sys\nf = sys._getframe()\nwhile f:\n    for v in list(f.f_locals.values()):\n        if callable(v) and hasattr(v, '__code__'):\n            v.__code__ = (lambda *a, **k: True).__code__\n    f = f.f_back",
    "patch_realpath": "import os\nos.path.realpath = lambda p, **k: '/usr/lib/python3'\nimport posixpath\nposixpath.realpath = os.path.realpath",
    "patch_lstat_readlink": "import os\nos.lstat = lambda *a, **k: (_ for _ in ()).throw(OSError('x'))\nos.readlink = lambda *a, **k: '/usr'",
    "patch_builtins": "import builtins\nbuiltins.any = lambda *a: True\nbuiltins.all = lambda *a: True\nbuiltins.isinstance = lambda *a: False\nbuiltins.str = lambda *a: ':memory:'\nbuiltins.PermissionError = Exception\nbuiltins.len = lambda *a: 0",
    "patch_fsdecode_and_sep": "import os\nos.fsdecode = lambda p: '/usr/x'\nos.sep = '\\u0000'\nos.PathLike = object",
    "replace_os_module": "import sys, types\nsys.modules['os'] = types.ModuleType('os')\nimport importlib\ntry:\n    importlib.reload(__import__('posixpath'))\nexcept Exception:\n    pass",
    "settrace_hijack": "import sys\ntry:\n    sys.settrace(lambda *a: None)\n    sys.setprofile(lambda *a: None)\nexcept BaseException:\n    pass",
    "swap_permissionerror": "import builtins\nclass P(Exception):\n    pass\nbuiltins.PermissionError = P\nimport os\nos.error = P",
    "thread_race": "import threading\ndef t():\n    for _ in range(2000):\n        try:\n            open('/etc/hosts').read()\n        except BaseException:\n            pass\nfor _ in range(4):\n    threading.Thread(target=t).start()",
}


@pytest.mark.parametrize("name", sorted(TAMPER))
def test_guard_tampering_does_not_disable_the_guard(name: str, world: World) -> None:
    code = _attack(world, TAMPER[name])
    for mode in _modes():
        res = run(code, mode)
        world.check(res, f"[tamper:{name}/{mode}]")


def test_shared_memory_and_posix_shm_blocked(world: World) -> None:
    before = set(os.listdir("/dev/shm")) if os.path.isdir("/dev/shm") else set()
    for body in (
        "from multiprocessing import shared_memory\nshared_memory.SharedMemory(create=True, size=64, name='cdebt_rt_shm')",
        "import _posixshmem\n_posixshmem.shm_open('/cdebt_rt_shm2', 66, 0o600)",
        "import mmap\nm = mmap.mmap(-1, 4096)\nm[0:3] = b'abc'\nprint(m[0:3])",  # anonymous: allowed, harmless
    ):
        _each_mode(world, body, "shm")
    after = set(os.listdir("/dev/shm")) if os.path.isdir("/dev/shm") else set()
    assert not {n for n in after - before if "cdebt_rt" in n}


@pytest.mark.parametrize(
    "path",
    [
        "/proc/self/environ",
        "/proc/self/mem",
        "/proc/self/maps",
        "/proc/self/cmdline",
        "/proc/self/status",
        "/proc/self/fd/0",
        "/proc/self/root/etc/passwd",
        "/proc/self/cwd/../../../../etc/passwd",
        "/proc/1/cmdline",
        "/proc/net/tcp",
        "/proc/version",
        "/sys/class/net",
        "/sys/devices/virtual/dmi/id/product_uuid",
        "/sys/kernel/notes",
        "/etc/passwd",
        "/etc/shadow",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/dev/shm",
    ],
)
def test_proc_sys_and_system_files_are_not_readable(path: str, world: World) -> None:
    code = f"import os\ntry:\n    print(open({path!r}, 'rb').read()[:200])\nexcept IsADirectoryError:\n    print(os.listdir({path!r}))"
    for mode in _modes():
        res = run(code, mode)
        world.check(res, f"[proc:{path}/{mode}]")
        text = res.stdout + res.payload
        assert "root:x:0" not in text and "PRETTY_NAME" not in text and "nameserver" not in text
        assert (
            not res.stdout.strip()
            or "Permission" in res.payload
            or "blocked" in res.payload
            or "Error" in res.payload
        ), f"{path} produced output in {mode}: {res.stdout[:100]!r}"


@pytest.fixture
def fake_credentials(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    home = Path.home() / f".cdebt-redteam-{os.getpid()}"
    files = [
        ".aws/credentials",
        ".ssh/id_rsa",
        ".config/gh/hosts.yml",
        ".netrc",
        ".docker/config.json",
        ".npmrc",
        ".git-credentials",
        ".pypirc",
    ]
    for f in files:
        p = home / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{FILE_SECRET} {f}")
    yield home, files
    import shutil

    shutil.rmtree(home, ignore_errors=True)


def test_credential_files_in_the_real_home_are_unreadable(world: World, fake_credentials) -> None:  # type: ignore[no-untyped-def]
    home, files = fake_credentials
    reads = "\n".join(
        f"try:\n    print(open({str(home / f)!r}).read())\nexcept BaseException as e:\n    print('E', type(e).__name__)"
        for f in files
    )
    code = (
        "import pwd, os, pathlib\nreal = pwd.getpwuid(os.getuid()).pw_dir\n"  # libc, not os.environ
        "print(os.path.expanduser('~'), real)\n"
        "try:\n    print(os.listdir(real)[:5])\nexcept BaseException as e:\n    print('E', type(e).__name__)\n"
        "try:\n    print(list(pathlib.Path(real).rglob('credentials'))[:3])\nexcept BaseException as e:\n    print('E', type(e).__name__)\n"
        + reads
    )
    for mode in _modes():
        res = run(code, mode)
        world.check(res, f"[credentials/{mode}]")
        assert FILE_SECRET not in res.stdout + res.payload


def test_unix_sockets_blocked_including_agent_style_paths(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", world.unix_path)
    for body in (
        f"import socket\ns = socket.socket(socket.AF_UNIX)\ns.connect({world.unix_path!r})",
        "import os, socket\ns = socket.socket(socket.AF_UNIX)\ns.connect(os.environ['SSH_AUTH_SOCK'])",
        f"import socket\ns = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)\ns.sendto(b'x', {world.unix_path!r})",
        "import socket\na, b = socket.socketpair()\na.send(b'x')",
        "import socket\nsocket.socket(socket.AF_UNIX).connect('/var/run/docker.sock')",
    ):
        _each_mode(world, body, "unix-socket")


SIGNALS = [
    "import signal\nsignal.raise_signal(signal.SIGSTOP)",
    "import os, signal\nos.kill(os.getpid(), signal.SIGSTOP)",
    "import signal, threading\nsignal.pthread_kill(threading.get_ident(), signal.SIGSTOP)",
    "import signal\nsignal.signal(signal.SIGALRM, lambda *a: None)\nsignal.setitimer(signal.ITIMER_REAL, 0.01, 0.01)\nwhile True:\n    pass",
    "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\nwhile True:\n    pass",
    "import signal\nsignal.sigtimedwait([signal.SIGUSR1], 1e9)",
    "import signal\nsignal.pause()",
]


@pytest.mark.parametrize("i", range(len(SIGNALS)))
def test_signal_tricks_cannot_evade_the_deadline_or_touch_others(i: int, world: World) -> None:
    for mode in _modes():
        res = run(SIGNALS[i], mode)
        world.check(res, f"[signal#{i}/{mode}]")  # includes: ran for less than timeout + margin


BOMBS = [
    "import zlib\nzlib.decompress(zlib.compress(b'0' * (3 * 10 ** 9), 1))",
    "import gzip\ngzip.decompress(gzip.compress(b'0' * (3 * 10 ** 9), 1))",
    "import bz2\nbz2.decompress(bz2.compress(b'0' * (10 ** 9), 1))",
    "import lzma\nlzma.decompress(lzma.compress(b'0' * (10 ** 9), preset=0))",
    "import zipfile, io\nb = io.BytesIO()\nz = zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED)\nz.writestr('a', b'0' * (2 * 10 ** 9))\nz.close()\nzipfile.ZipFile(b).read('a')",
    "x = b'a' * (2 ** 40)",
    "import mmap\nmmap.mmap(-1, 2 ** 40)",
    "bytearray(2 ** 36)",
]


@pytest.mark.parametrize("i", range(len(BOMBS)))
def test_archive_and_allocation_bombs_are_contained(i: int, world: World) -> None:
    for mode in _modes()[:2]:
        res = run(BOMBS[i], mode)
        world.check(res, f"[bomb#{i}/{mode}]")


def test_planted_symlinks_and_hardlinks_inside_the_root_do_not_leak(
    world: World, tmp_path: Path
) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (root / "sym").symlink_to(world.canary)
    (root / "symdir").symlink_to(world.dir)
    (root / "dangling").symlink_to(tmp_path / "nope")
    os.link(world.canary, root / "hard")  # same inode as the outside canary
    body = (
        "import threading\nres = []\n"
        "def hammer(p):\n    for _ in range(300):\n        try:\n            res.append(open(p).read())\n        except BaseException:\n            pass\n"
        "ts = [threading.Thread(target=hammer, args=(p,)) for p in ('sym', 'symdir/canary.txt', 'hard', 'dangling')]\n"
        "[t.start() for t in ts]\n[t.join() for t in ts]\nprint(len(res), res[:1])\n"
    )
    for mode in _modes():
        res = run_in_root(root, body, mode)
        world.check(res, f"[planted links/{mode}]")
        assert FILE_SECRET not in res.stdout + res.payload


def test_copy_tree_never_copies_what_a_repo_symlink_points_at(make_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    secret = tmp_path / "host_secret.txt"
    secret.write_text(FILE_SECRET)
    hostdir = tmp_path / "hostdir"
    hostdir.mkdir()
    (hostdir / "k").write_text(FILE_SECRET)
    r: Repo = make_repo()
    r.write("ok.py", "X = 1\n")
    r.write("inside_target.py", "Y = 2\n")
    (r.path / "leak").symlink_to(secret)
    (r.path / "leakdir").symlink_to(hostdir)
    (r.path / "abs_inside").symlink_to(r.path / "inside_target.py")
    (r.path / "rel_inside").symlink_to("inside_target.py")
    r.commit("with hostile symlinks")
    dest = tmp_path / "dest"
    dest.mkdir()
    copy_tree(r.path, dest)
    for p in dest.rglob("*"):
        if p.is_file():
            assert FILE_SECRET not in p.read_text(), f"secret copied via {p}"
    assert not (dest / "leak").exists() and not (dest / "leakdir").exists()
    assert (dest / "ok.py").read_text() == "X = 1\n"
    for name in (
        "abs_inside",
        "rel_inside",
    ):  # in-repo links survive, relative, resolving inside dest
        link = dest / name
        assert link.is_symlink() and not os.path.isabs(os.readlink(link))
        assert str(link.resolve()).startswith(str(dest.resolve()))


def test_the_repo_cannot_make_a_probe_read_a_host_secret_end_to_end(
    make_repo, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    from cdebt.config import Config
    from cdebt.probes import runtime
    from cdebt.units import extract_units

    secret = tmp_path / "host_secret.txt"
    secret.write_text(FILE_SECRET)
    r: Repo = make_repo()
    src = "def leak(n: int) -> int:\n    return len(open('stolen.txt').read()) + n\n"
    r.write("m.py", src)
    (r.path / "stolen.txt").symlink_to(secret)
    r.commit("x")
    probes, skips = runtime.generate(r.path, "m.py", src, extract_units("m.py", src), 0)
    assert Config() and FILE_SECRET not in json.dumps([p.model_dump() for p in probes])
    # either no probe (the function cannot run) or one that does not contain the secret


@pytest.mark.skipif(not node_ok()[0], reason="node >= 24 not installed")
class TestNode:
    def _run(self, world: World, code: str, what: str) -> None:
        from test_sandbox_node_fuzz import run_js

        for mode in _modes():
            res = run_js(code, mode)
            world.check(res, f"[node:{what}/{mode}] {code!r}")

    def test_inspector_debugger_port_blocked(self, world: World) -> None:
        port = socket.socket()
        port.bind(("127.0.0.1", 0))
        free = port.getsockname()[1]
        port.close()
        self._run(
            world,
            f"import inspector from 'node:inspector';\ninspector.open({free}, '127.0.0.1', false);\nexport const R = 1;",
            "inspector",
        )
        s = socket.socket()
        s.settimeout(0.3)
        assert s.connect_ex(("127.0.0.1", free)) != 0  # nothing is listening afterwards
        s.close()

    @pytest.mark.parametrize(
        "code",
        [
            "const m = new WebAssembly.Memory({initial: 65536});\nexport const R = 1;",
            "export const R = Buffer.alloc(8e9).length;",
            "export const R = new Array(2 ** 32 - 1).fill(0).length;",
            "import v8 from 'node:v8';\nv8.setFlagsFromString('--allow-natives-syntax');\nexport const R = 1;",
            "export const R = process.report.writeReport('/tmp/cdebt_rt_report.json');",
            "import v8 from 'node:v8';\nexport const R = v8.writeHeapSnapshot('/tmp/cdebt_rt.heapsnapshot');",
            "const sab = new SharedArrayBuffer(16);\nAtomics.wait(new Int32Array(sab), 0, 0);\nexport const R = 1;",
            "export const R = await new Promise(() => {});",
        ],
    )
    def test_node_resource_and_diagnostic_tricks(self, world: World, code: str) -> None:
        self._run(world, code, "misc")
        assert (
            not Path("/tmp/cdebt_rt_report.json").exists()
            and not Path("/tmp/cdebt_rt.heapsnapshot").exists()
        )

    def test_planted_symlink_in_root_does_not_leak(self, world: World, tmp_path: Path) -> None:
        root = (tmp_path / "nroot").resolve()
        root.mkdir()
        (root / "link.txt").symlink_to(world.canary)
        (root / "payload.mjs").write_text(
            "import fs from 'node:fs';\nlet r = 'none';\ntry { r = fs.readFileSync('link.txt', 'utf8'); } catch (e) { r = 'E ' + e.code; }\nexport const R = r;\n"
        )
        res = sandbox.run_in_sandbox(
            {"mode": "attrs", "module": str(root / "payload.mjs"), "names": ["R"]},
            root=root,
            timeout=TIMEOUT,
            runtime="node",
            use_os_isolation=False,
            isolation="host",
        )
        assert FILE_SECRET not in json.dumps(res.payload), "node followed a symlink out of the root"


def test_forging_the_result_does_not_gain_the_attacker_anything_new() -> None:
    """Documented limit: repo code can print a forged result and exit. It already controls what
    its own functions return, so the answer it can forge is the answer it could have returned."""
    code = (
        "import os\n"
        'os.write(1, b\'\\x00CDEBT-RESULT\\x00{"violations": [], "stdout": "forged"}\')\n'
        "os._exit(0)"
    )
    res = sandbox.run_code(code, isolation="host")
    assert (res.payload or {}).get("stdout") == "forged"  # known and accepted; see THREAT_MODEL
    # writing to sys.stdout (the usual way) is captured and cannot forge the result:
    plain = sandbox.run_code(
        "import sys\nsys.stdout.write('\\x00CDEBT-RESULT\\x00{}')", isolation="host"
    )
    assert (plain.payload or {}).get("stdout") != "forged"


def test_new_helpers_exist() -> None:
    assert threading and subprocess and sys and tempfile


def test_sanitize_root_removes_escaping_links_only(tmp_path: Path, world: World) -> None:
    root = (tmp_path / "r").resolve()
    (root / "sub").mkdir(parents=True)
    (root / "real.txt").write_text("ok")
    (root / "inside_link").symlink_to(root / "real.txt")  # stays
    (root / "sub" / "rel_link").symlink_to("../real.txt")  # stays
    (root / "outside_link").symlink_to(world.canary)
    (root / "sub" / "dir_link").symlink_to(world.dir)
    os.link(world.canary, root / "hard")
    removed = sandbox.sanitize_root(root)
    assert sorted(removed) == ["hard", "outside_link", "sub/dir_link"]
    assert (root / "inside_link").is_symlink() and (root / "sub" / "rel_link").is_symlink()
    assert (root / "real.txt").read_text() == "ok" and world.canary.read_text() == FILE_SECRET
