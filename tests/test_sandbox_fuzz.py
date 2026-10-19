"""Property/fuzz tests for the sandbox. Random hostile payloads are composed from families
(fs writes, fs reads, network, processes/signals, resource exhaustion, env leaks, path tricks) and
every run must leave the world untouched:

  * outside files unchanged, no new files appear next to the sandbox,
  * nothing connects to local TCP/UDP/unix listeners,
  * a sentinel process (a stand-in for "your other processes") is still alive,
  * no canary secret (file content or env var) shows up in any output,
  * the run returns within timeout + margin.

Deterministic: seeded. CDEBT_FUZZ_N (iterations per family, default 6) and CDEBT_FUZZ_SEED scale it
up for soak runs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from cdebt import sandbox

N = int(os.environ.get("CDEBT_FUZZ_N", "6"))
SEED = int(os.environ.get("CDEBT_FUZZ_SEED", "20261019"))
FILE_SECRET = "FILE-CANARY-9f3a1c"
ENV_SECRET = "ENV-CANARY-77be02"
TIMEOUT = 3.0
MARGIN = 4.0


class World:
    """Everything a payload must not be able to touch, plus detectors."""

    def __init__(self, base: Path) -> None:
        self.dir = base / "outside"
        self.dir.mkdir()
        self.canary = self.dir / "canary.txt"
        self.canary.write_text(FILE_SECRET)
        self.victim = self.dir / "victim.txt"
        self.victim.write_text("original")
        self.tmp_marker = f"cdebt-fuzz-{os.getpid()}"
        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen(5)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.05)
        self.sockdir = tempfile.mkdtemp(prefix="cdf", dir="/tmp")  # AF_UNIX paths are short
        self.unix_path = os.path.join(self.sockdir, "u.sock")
        self.unix = socket.socket(socket.AF_UNIX)
        self.unix.bind(self.unix_path)
        self.unix.listen(5)
        self.hits: list[str] = []
        self.sentinel = subprocess.Popen(["sleep", "300"])
        self._stop = False
        for s, name in ((self.tcp, "tcp"), (self.unix, "unix")):
            threading.Thread(target=self._accept, args=(s, name), daemon=True).start()
        self.snapshot_before = self._snapshot()

    def _accept(self, srv: socket.socket, name: str) -> None:
        while not self._stop:
            try:
                srv.settimeout(0.2)
                c, _ = srv.accept()
                self.hits.append(name)
                c.close()
            except OSError:
                continue

    @property
    def tcp_port(self) -> int:
        return int(self.tcp.getsockname()[1])

    @property
    def udp_port(self) -> int:
        return int(self.udp.getsockname()[1])

    def _snapshot(self) -> dict[str, str]:
        out = {}
        for p in sorted(self.dir.rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        out["__listing__"] = ",".join(sorted(os.listdir(self.dir)))
        out["__tmp__"] = ",".join(
            sorted(n for n in os.listdir(tempfile.gettempdir()) if self.tmp_marker in n)
        )
        return out

    def check(self, res: Res, what: str) -> None:
        assert res.died is None, f"the payload killed its parent/worker: {res.died} :: {what}"
        try:
            self.udp.recvfrom(10)
            self.hits.append("udp")
        except OSError:
            pass
        text = f"{res.stdout}\n{res.stderr}\n{res.payload}"
        assert FILE_SECRET not in text, f"file secret leaked: {what}"
        assert ENV_SECRET not in text, f"env secret leaked: {what}"
        assert self._snapshot() == self.snapshot_before, f"filesystem changed: {what}"
        assert not self.hits, f"a connection reached a local listener ({self.hits}): {what}"
        assert self.sentinel.poll() is None, f"sentinel process was killed: {what}"
        assert res.elapsed < TIMEOUT + MARGIN, f"ran too long ({res.elapsed:.1f}s): {what}"

    def close(self) -> None:
        self._stop = True
        self.sentinel.kill()
        self.sentinel.wait()
        for s in (self.tcp, self.udp, self.unix):
            s.close()
        with contextlib.suppress(OSError):
            os.unlink(self.unix_path)
            os.rmdir(self.sockdir)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CDEBT_CANARY_SECRET", ENV_SECRET)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", ENV_SECRET)
    w = World(tmp_path)
    yield w
    w.close()


MODES = ["audit", "host", "strong"]


def _modes() -> list[str]:
    ms = ["audit", "host"]
    if sandbox._sandbox_exec_available() or sandbox.docker_available():
        ms.append("strong")
    return ms


class Res:
    """Result of one payload run in a worker process."""

    def __init__(self, d: dict, died: str | None) -> None:  # type: ignore[type-arg]
        self.died = died
        self.stdout = d.get("stdout", "")
        self.stderr = d.get("stderr", "")
        self.payload = d.get("payload", "")
        self.elapsed = float(d.get("elapsed", 0.0))
        self.timed_out = bool(d.get("timed_out"))
        self.killed_for_output = bool(d.get("killed_for_output"))
        self.killed_for_memory = bool(d.get("killed_for_memory"))
        self.violations = d.get("violations", [])
        self.stdout_len = int(d.get("stdout_len", 0))


def run(code: str, mode: str, timeout: float = TIMEOUT) -> Res:
    """Run `code` through the sandbox in a disposable worker process."""
    p = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_fuzz_worker.py"))],
        input=json.dumps({"code": code, "mode": mode, "timeout": timeout}),
        capture_output=True,
        text=True,
        timeout=timeout + 60,
    )
    if p.returncode != 0:
        return Res({}, f"worker died (rc={p.returncode}): {p.stderr[-300:]}")
    return Res(json.loads(p.stdout.strip().splitlines()[-1]), None)


# ---- payload families -----------------------------------------------------------------------
def fs_write(rng: random.Random, w: World) -> str:
    target = rng.choice(
        [
            str(w.victim),
            str(w.dir / "new.txt"),
            str(w.dir / ".." / "outside" / "new2.txt"),
            f"{tempfile.gettempdir()}/{w.tmp_marker}-w",
            f"/dev/null/../{w.tmp_marker}",
            str(Path.home() / f".{w.tmp_marker}"),
            f"../{w.tmp_marker}-rel",
            f"./{w.tmp_marker}-cwd",
        ]
    )
    t = repr(target)
    return rng.choice(
        [
            f"open({t}, 'w').write('pwn')",
            f"open({t}, 'a').write('pwn')",
            f"open({t}, 'x').write('pwn')",
            f"open({t}, 'r+').write('pwn')",
            f"import os\nos.close(os.open({t}, os.O_CREAT | os.O_WRONLY))",
            f"from pathlib import Path\nPath({t}).write_text('pwn')",
            f"from pathlib import Path\nPath({t}).touch()",
            f"import shutil\nshutil.copyfile(__file__ if False else '/etc/hosts', {t})",
            f"import os\nos.rename({str(w.victim)!r}, {t})",
            f"import os\nos.replace({str(w.victim)!r}, {t})",
            f"import os\nos.remove({str(w.victim)!r})",
            f"import os\nos.truncate({str(w.victim)!r}, 0)",
            f"import os\nos.utime({str(w.victim)!r}, (0, 0))",
            f"import os\nos.chmod({str(w.victim)!r}, 0o777)",
            f"import os\nos.mkdir({t})",
            f"import os\nos.symlink('/etc/hosts', {t})",
            f"import os\nos.link({str(w.victim)!r}, {t})",
            f"import tempfile\ntempfile.mkstemp(dir={str(w.dir)!r})",
            f"import io\nio.open({t}, 'w').write('x')",
            f"import codecs\ncodecs.open({t}, 'w').write('x')",
            f"import sqlite3\nsqlite3.connect({t}).execute('create table t(x)')",
            f"import dbm\ndbm.open({t}, 'c')",
            f"import shelve\nshelve.open({t})",
            f"import logging\nlogging.FileHandler({t})",
            f"import zipfile\nzipfile.ZipFile({t}, 'w').writestr('a', 'b')",
            f"import shutil\nshutil.rmtree({str(w.dir)!r})",
            f"import os\nos.mkfifo({t})",
        ]
    )


def fs_read(rng: random.Random, w: World) -> str:
    target = rng.choice(
        [
            str(w.canary),
            str(w.dir / ".." / "outside" / "canary.txt"),
            str(w.dir) + "//canary.txt",
            str(w.dir) + "/./canary.txt",
            "/dev/../" + str(w.canary).lstrip("/"),
        ]
    )
    t = repr(target)
    return rng.choice(
        [
            f"print(open({t}).read())",
            f"print(open({t}, 'rb').read())",
            f"from pathlib import Path\nprint(Path({t}).read_text())",
            f"import os\nfd = os.open({t}, os.O_RDONLY)\nprint(os.read(fd, 100))",
            f"import io\nprint(io.open({t}).read())",
            f"import os\nprint(os.listdir({str(w.dir)!r}))",
            f"import os\nprint(list(os.scandir({str(w.dir)!r})))",
            f"import glob\nprint(glob.glob({str(w.dir) + '/*'!r}))",
            f"import sqlite3\nc = sqlite3.connect('file:{target}?mode=ro', uri=True)\nprint(c.execute('select * from sqlite_master').fetchall())",
            f"import runpy\nrunpy.run_path({t})",
            f"exec(compile(open({t}).read(), 'x', 'exec'))",
            f"import linecache\nprint(linecache.getlines({t}))",
            f"import fileinput\nprint(list(fileinput.input({t})))",
            f"import mmap, os\nfd = os.open({t}, os.O_RDONLY)\nprint(mmap.mmap(fd, 0, access=mmap.ACCESS_READ)[:50])",
            "print(open('/etc/hosts').read())",
            "print(open('/proc/self/environ').read())",
            f"print(open({str(Path.home() / '.ssh' / 'id_rsa')!r}).read())",
        ]
    )


def net(rng: random.Random, w: World) -> str:
    p, u, s = w.tcp_port, w.udp_port, w.unix_path
    return rng.choice(
        [
            f"import socket\nsocket.create_connection(('127.0.0.1', {p}))",
            f"import socket\ns = socket.socket()\ns.connect(('127.0.0.1', {p}))",
            f"import urllib.request\nurllib.request.urlopen('http://127.0.0.1:{p}/')",
            f"import http.client\nhttp.client.HTTPConnection('127.0.0.1', {p}).request('GET', '/')",
            f"import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.sendto(b'x', ('127.0.0.1', {u}))",
            f"import socket\ns = socket.socket(socket.AF_UNIX)\ns.connect({s!r})",
            f"import asyncio\nasyncio.run(asyncio.open_connection('127.0.0.1', {p}))",
            "import socket\nsocket.getaddrinfo('example.com', 80)",
            "import socket\nsocket.gethostbyname('example.com')",
            f"import smtplib\nsmtplib.SMTP('127.0.0.1', {p})",
            "import ftplib\nftplib.FTP('127.0.0.1')",
            "import socket\ns = socket.socket()\ns.bind(('127.0.0.1', 0))\ns.listen()",
            f"import ssl, socket\nssl.create_default_context().wrap_socket(socket.socket(), server_hostname='x').connect(('127.0.0.1', {p}))",
        ]
    )


def proc(rng: random.Random, w: World) -> str:
    m = repr(f"{tempfile.gettempdir()}/{w.tmp_marker}-p")
    return rng.choice(
        [
            "import os\nos.fork()",
            f"import os\nos.system('touch ' + {m})",
            f"import subprocess\nsubprocess.run(['touch', {m}])",
            f"import subprocess\nsubprocess.Popen('touch ' + {m}, shell=True)",
            "import subprocess\nsubprocess.check_output(['sh', '-c', 'id'])",
            f"import os\nos.execv('/usr/bin/touch', ['touch', {m}])",
            f"import os\nos.posix_spawn('/usr/bin/touch', ['touch', {m}], os.environ)",
            f"import os\nos.spawnl(os.P_NOWAIT, '/usr/bin/touch', 'touch', {m})",
            "import multiprocessing\nmultiprocessing.Process(target=print).start()",
            "import pty\npty.fork()",
            "import ctypes\nctypes.CDLL(None).system(b'id')",
            "import os\nos.popen('id').read()",
            "import os\nos.fork()\nos.fork()",
            # signals: must never reach the sentinel or our own process
            f"import os\nos.kill({w.sentinel.pid}, 9)",
            f"import os\nos.killpg({os.getpgid(w.sentinel.pid)}, 9)",
            "import os\nos.kill(os.getppid(), 9)",
            "import os, signal\nos.kill(0, signal.SIGKILL)",  # own process group only
            f"import signal\nsignal.pthread_kill(0, 9) if False else __import__('os').kill({w.sentinel.pid}, 15)",
        ]
    )


def resource_(rng: random.Random, w: World) -> str:
    return rng.choice(
        [
            "while True:\n    pass",
            "import time\ntime.sleep(1e9)",
            "x = []\nwhile True:\n    x.append(bytearray(10_000_000))",
            "x = bytes(2 ** 40)",
            "def f(n):\n    return f(n + 1)\nf(0)",
            "import sys\nsys.setrecursionlimit(10 ** 9)\ndef f(n):\n    return f(n + 1)\nf(0)",
            "while True:\n    print('x' * 100000)",
            "import sys\nwhile True:\n    sys.stderr.write('x' * 100000)",
            "import os\nfds = []\nwhile True:\n    fds.append(os.open('/dev/null', os.O_RDONLY))",
            "import os\nps = []\nwhile True:\n    ps.append(os.pipe())",
            "import threading, time\nfor _ in range(2000):\n    threading.Thread(target=time.sleep, args=(1e9,)).start()",
            "import threading\nwhile True:\n    threading.Thread(target=lambda: None).start()",
            "import os\nos._exit(3)",
            "import signal, os\nsignal.alarm(1)\nwhile True:\n    pass",
            "import faulthandler\nfaulthandler._sigsegv()",
            "import ctypes\nctypes.string_at(0)",
            "d = {}\nwhile True:\n    d[len(d)] = 'x' * 1000",
        ]
    )


def env_leak(rng: random.Random, w: World) -> str:
    return rng.choice(
        [
            "import os\nprint(dict(os.environ))",
            "import os\nprint(os.environ.get('CDEBT_CANARY_SECRET'), os.getenv('AWS_SECRET_ACCESS_KEY'))",
            "import os\nprint(os.environb)",
            "print(open('/proc/self/environ').read())",
            "import os\nprint(open(f'/proc/{os.getppid()}/environ').read())",
            "import subprocess\nprint(subprocess.check_output(['env']))",
            "import os\nprint(os.popen('printenv').read())",
            "import sys\nprint(sys.argv, sys.path)",
            "import os\nprint(os.environ['HOME'], os.getcwd())",
        ]
    )


def path_tricks(rng: random.Random, w: World) -> str:
    name = w.tmp_marker
    return rng.choice(
        [
            f"open('/dev/../{w.canary}'.replace('//', '/')).read()",
            f"open('/proc/self/cwd/../../../../../../{str(w.canary).lstrip('/')}').read()",
            f"open('{w.canary}\\x00.txt').read()",
            "open('/' + 'a' * 5000).read()",
            f"import os\nos.chdir('/')\nopen({str(w.dir)!r} + '/new.txt', 'w').write('x')",
            f"import os\nos.chdir({str(w.dir)!r})\nopen('victim.txt', 'w').write('x')",
            f"open('/tmp/../{name}', 'w')",
            f"open(__import__('os').path.expanduser('~/../{name}'), 'w')",
            f"import os\nos.chdir('..')\nopen('{name}-up', 'w').write('x')",
        ]
    )


FAMILIES = {
    "fs_write": fs_write,
    "fs_read": fs_read,
    "net": net,
    "proc": proc,
    "resource": resource_,
    "env": env_leak,
    "path": path_tricks,
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_fuzz_family(family: str, world: World) -> None:
    rng = random.Random(f"{SEED}-{family}")
    modes = _modes()
    for i in range(N):
        code = FAMILIES[family](rng, world)
        mode = modes[i % len(modes)]
        res = run(code, mode)
        world.check(res, f"[{family}/{mode}/#{i}] {code!r}")


def test_fuzz_combined_payloads(world: World) -> None:
    """Several hostile fragments in one payload, each wrapped so one failure does not stop the
    next: the attack surface is the union, and the invariants must still hold."""
    rng = random.Random(f"{SEED}-combo")
    modes = _modes()
    for i in range(max(3, N // 2)):
        frags = [
            FAMILIES[f](rng, world) for f in rng.sample(sorted(FAMILIES), 4) if f != "resource"
        ]
        code = "\n".join(
            "try:\n"
            + "\n".join("    " + ln for ln in fr.splitlines())
            + "\nexcept BaseException:\n    pass"
            for fr in frags
        )
        res = run(code, modes[i % len(modes)])
        world.check(res, f"[combo/#{i}] {code!r}")


def run_in_root(root: Path, body: str, mode: str) -> Res:
    spec = {"code": body, "mode": mode, "timeout": TIMEOUT, "root": str(root)}
    p = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_fuzz_worker.py"))],
        input=json.dumps(spec),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if p.returncode != 0:
        return Res({}, f"worker died (rc={p.returncode}): {p.stderr[-300:]}")
    return Res(json.loads(p.stdout.strip().splitlines()[-1]), None)


def test_symlinks_planted_inside_the_sandbox_root(world: World, tmp_path: Path) -> None:
    """A symlink that already exists in the sandbox root and points outside must not let a
    payload read or write through it."""
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (root / "out").symlink_to(world.dir)
    (root / "canary_link").symlink_to(world.canary)
    for mode in _modes():
        for body in (
            "print(open('canary_link').read())",
            "open('out/new.txt', 'w').write('x')",
            "open('out/victim.txt', 'w').write('x')",
            "import os\nprint(os.listdir('out'))",
            "import os\nos.remove('out/victim.txt')",
        ):
            res = run_in_root(root, body, mode)
            world.check(res, f"[symlink/{mode}] {body!r}")


def test_oversized_output_is_cut_off(world: World) -> None:
    res = run("import sys\nwhile True:\n    sys.stdout.write('y' * 1000000)", "host", timeout=10)
    assert res.killed_for_output or res.timed_out or res.killed_for_memory
    assert res.stdout_len <= sandbox.MAX_OUTPUT + 70000
    world.check(res, "output flood")


def test_signals_cannot_reach_other_processes_even_without_the_audit_hook(world: World) -> None:
    """OS-level check (no Python hook): with the strict Seatbelt profile a process cannot signal
    others. Skipped where Seatbelt is unavailable."""
    if not sandbox._sandbox_exec_available():
        pytest.skip("sandbox-exec not available")
    prof = sandbox._sbx_profile(str(Path(tempfile.gettempdir())), strict=True, allow_fork=True)
    p = subprocess.run(
        ["sandbox-exec", "-p", prof, "/bin/kill", "-9", str(world.sentinel.pid)],
        capture_output=True,
        text=True,
    )
    time.sleep(0.2)
    assert world.sentinel.poll() is None, f"sentinel was killed (rc={p.returncode})"


def test_seatbelt_strict_hides_home_without_the_python_hook(world: World) -> None:
    if not sandbox._sandbox_exec_available():
        pytest.skip("sandbox-exec not available")
    secret = Path.home() / f".{world.tmp_marker}-secret"
    secret.write_text(FILE_SECRET)
    try:
        prof = sandbox._sbx_profile(str(Path(tempfile.gettempdir())), strict=True, allow_fork=True)
        p = subprocess.run(
            ["sandbox-exec", "-p", prof, "/bin/cat", str(secret)], capture_output=True, text=True
        )
        assert FILE_SECRET not in p.stdout and p.returncode != 0
    finally:
        secret.unlink()
