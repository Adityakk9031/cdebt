"""Red team for the fake-world policy: it lets code write inside its scratch root, so every other
way out (outside paths, links, renames, processes, sockets, native code, disk filling) must stay
shut. Each attack runs with the audit guard alone and with the OS layer on top."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cdebt import sandbox
from cdebt.sandbox import run_in_sandbox
from cdebt.world import World, runner_spec

pytestmark = pytest.mark.posix_sandbox


def _run(tmp_path: Path, code: str, *, os_layer: bool, fake: bool = False, timeout: float = 20):  # type: ignore[no-untyped-def]
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    spec: dict[str, object] = {"mode": "exec", "code": code}
    if fake:
        spec.update(fake_world=True, world=runner_spec(World()))
    return run_in_sandbox(
        spec, root=root, policy="world", timeout=timeout, use_os_isolation=os_layer
    )


LAYERS = pytest.mark.parametrize("os_layer", [False, True], ids=["audit", "audit+os"])
FAKES = pytest.mark.parametrize("fake", [False, True], ids=["bare", "fake-world"])


@LAYERS
@FAKES
def test_writes_inside_the_scratch_root_work(tmp_path, os_layer, fake) -> None:  # type: ignore[no-untyped-def]
    res = _run(
        tmp_path,
        "import os\nos.makedirs('a/b')\nopen('a/b/f.txt','w').write('hi')\n"
        "print(open('a/b/f.txt').read())\nos.rename('a/b/f.txt','a/g.txt')\nos.remove('a/g.txt')\n",
        os_layer=os_layer,
        fake=fake,
    )
    assert not res.violations and (res.payload or {}).get("stdout") == "hi\n"


ATTACKS = {
    "absolute write": "open('{out}/abs.txt','w').write('x')",
    "dotdot write": "open('../outside_dd.txt','w').write('x')",
    "symlink then write": "import os\nos.symlink('{out}/seed.txt','ln')\nopen('ln','w').write('p')",
    "hardlink to outside": "import os\nos.link('{out}/seed.txt','hl')\nopen('hl','w').write('pwn')",
    "rename out": "import os\nopen('f','w').write('x')\nos.rename('f','{out}/moved.txt')",
    "copy out": "import shutil\nopen('f','w').write('x')\nshutil.copy('f','{out}/copied.txt')",
    "mkdir outside": "import os\nos.mkdir('{out}/newdir')",
    "chmod outside": "import os\nos.chmod('{out}/seed.txt',0o777)",
    "fork": "import os\nos.fork()",
    "posix_spawn": "import os\nos.posix_spawn('/usr/bin/touch',['touch','{out}/spawned'],{{}})",
    "exec": "import os\nos.execv('/usr/bin/touch',['touch','{out}/execd'])",
    "socket": "import socket\nsocket.create_connection(('127.0.0.1', 9))",
    "dns": "import socket\nsocket.getaddrinfo('example.com', 80)",
    "ctypes": "import ctypes\nctypes.CDLL(None).system(b'touch {out}/ctyped')",
    "open fd write outside": "import os\nos.open('{out}/fd.txt', os.O_WRONLY | os.O_CREAT)",
    "truncate outside": "import os\nos.truncate('{out}/seed.txt', 0)",
}


@LAYERS
@FAKES
@pytest.mark.parametrize("name", sorted(ATTACKS))
def test_every_other_way_out_stays_shut(tmp_path, os_layer, fake, name) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "outside"
    out.mkdir()
    seed = out / "seed.txt"
    seed.write_text("seed")
    seed.chmod(0o644)
    res = _run(tmp_path, ATTACKS[name].format(out=out), os_layer=os_layer, fake=fake)
    assert (res.payload or {}).get("error") or res.violations, name
    assert [p.name for p in out.iterdir()] == ["seed.txt"], name  # nothing new outside
    assert seed.read_text() == "seed" and seed.stat().st_mode & 0o777 == 0o644, name
    assert not (tmp_path / "outside_dd.txt").exists()
    assert not (tmp_path / "root" / "hl").exists() and not (tmp_path / "root" / "ln").exists()


def test_fake_subprocess_never_spawns_anything(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "outside"
    out.mkdir()
    res = _run(
        tmp_path,
        "import subprocess, os\nsubprocess.run(['touch','{o}/a'])\nos.system('touch {o}/b')\n"
        "subprocess.Popen('touch {o}/c', shell=True)\nprint('done')".replace("{o}", str(out)),
        os_layer=False,
        fake=True,
    )
    assert not list(out.iterdir()), "a fake subprocess must not run anything"
    assert (res.payload or {}).get("stdout") == "done\n"


def test_a_file_bigger_than_the_limit_is_cut_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    res = _run(
        tmp_path,
        "f = open('big','wb')\nfor _ in range(40):\n    f.write(b'x' * (1 << 20))\n    f.flush()\n",
        os_layer=False,
    )
    assert res.returncode != 0 or (res.payload or {}).get("error")
    size = (tmp_path / "root" / "big").stat().st_size if (tmp_path / "root" / "big").exists() else 0
    assert size <= 16 << 20


def test_filling_the_scratch_disk_is_stopped(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(sandbox, "MAX_SCRATCH", 3 << 20)
    res = _run(
        tmp_path,
        "import time\nfor i in range(60):\n    open(f'f{i}','wb').write(b'x' * (1 << 20))\n"
        "    time.sleep(0.2)\n",
        os_layer=False,
        timeout=30,
    )
    assert res.killed_for_output
    assert sandbox._tree_bytes(tmp_path / "root") < 30 << 20
    assert os.path.exists(tmp_path)
