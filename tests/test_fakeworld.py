"""The fake world: functions that read the clock, random numbers or the environment can be run for
real in the sandbox because those inputs are fixed. Writes, network and processes stay blocked."""

from __future__ import annotations

import pytest

from cdebt.probes import runtime
from cdebt.sandbox import FAKE_EPOCH, fake_world_uses, screen_function, world_note
from cdebt.units import extract_units
from conftest import Repo

CODE = """import os
import random
import subprocess
import time
from datetime import datetime


def stamp(n: int) -> int:
    return int(time.time()) + n


def year(n: int) -> int:
    return datetime.now().year + n


def pick(n: int) -> bool:
    random.shuffle([1, 2, 3])
    return random.random() < 2 + n


def home(n: int) -> int:
    return len(os.environ.get("CDEBT_NOT_SET", "x")) + n


def run(n: int) -> int:
    return subprocess.call(["true"]) + n


def remove(n: int) -> int:
    os.remove("/nope")
    return n


def wait(n: int) -> int:
    time.sleep(30)
    return n
"""


def test_static_screen_still_flags_side_effects() -> None:
    assert screen_function(CODE, "stamp") != []  # off by default
    for name in ("stamp", "year", "pick", "home", "wait"):
        assert screen_function(CODE, name, fake_world=True) == [], name
    assert screen_function(CODE, "run", fake_world=True)  # subprocess is never faked
    assert screen_function(CODE, "remove", fake_world=True)  # os.remove is not an env helper


def test_uses_and_note() -> None:
    assert fake_world_uses(CODE, "stamp") == ["clock"]
    assert fake_world_uses(CODE, "pick") == ["random"]
    assert fake_world_uses(CODE, "home") == ["env"]
    assert fake_world_uses(CODE, "run") == []
    assert world_note([]) == ""
    assert "2020-01-01" in world_note(["clock"])


@pytest.mark.posix_sandbox
def test_runtime_probes_run_with_the_fake_world(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/__init__.py", "")
    r.write("pkg/w.py", CODE)
    r.commit("c")
    units = extract_units("pkg/w.py", CODE)
    probes, skips = runtime.generate(r.path, "pkg/w.py", CODE, units, seed=1, per_unit=2)
    by = {p.symbol: p for p in probes}
    # the clock really is frozen: stamp(n) = FAKE_EPOCH + n for whatever n was drawn
    s = by["stamp"]
    n = int(s.question.split("stamp(")[1].split(")")[0])
    assert s.answer == str(int(FAKE_EPOCH) + n)
    assert "2020-01-01" in s.question
    y = by["year"]
    assert y.answer == str(2020 + int(y.question.split("year(")[1].split(")")[0]))
    assert "pick" in by and "home" in by
    assert by["pick"].answer == "True"
    assert by["wait"].answer  # sleep(30) returned instantly instead of timing out
    assert by["stamp"].evidence["fake_world"] == ["clock"]
    # process spawning is faked too (nothing is spawned: the fake exits 0)
    assert by["run"].answer == str(int(by["run"].question.split("run(")[1].split(")")[0]))
    assert by["run"].evidence["fake_world"] == ["commands"]
    # a write outside the scratch copy is still blocked, so that function is skipped
    assert "remove" not in by
    assert any("remove" in s and "blocked" in s for s in skips)
