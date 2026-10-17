"""The sandbox timeout is a safety property. Regression tests for every way the kill path could
miss or be late (see docs/VERIFICATION.md, "Infinite-loop flake")."""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cdebt import sandbox
from cdebt.sandbox import run_code, run_in_sandbox

LOOP = "while True:\n    pass\n"


def _burn() -> None:
    while True:
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_does_not_wait_for_a_slow_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old watchdog polled RSS (`ps`) and checked the deadline in the SAME loop, so one slow `ps`
    (common under load) delayed the kill by that long. The deadline is now its own timer."""
    monkeypatch.setattr(sandbox, "_rss_kb", lambda pid: (time.sleep(4), 0)[1])
    r = run_code(LOOP, timeout=1.0, os_isolation=False)
    assert r.timed_out and r.elapsed < 1.0 + 0.7, f"killed late: {r.elapsed:.2f}s"


def test_timeout_holds_under_cpu_stress() -> None:
    burners = [mp.Process(target=_burn, daemon=True) for _ in range((os.cpu_count() or 4) * 2)]
    for b in burners:
        b.start()
    try:
        time.sleep(0.5)
        for _ in range(3):
            r = run_code(LOOP, timeout=1.0, os_isolation=False)
            assert r.timed_out and r.elapsed < 1.0 + 2.5, f"{r.elapsed:.2f}s"
    finally:
        for b in burners:
            b.terminate()


def test_detached_child_does_not_survive_timeout(tmp_path: Path) -> None:
    """A child that leaves the process group (setsid) used to outlive the timeout (until its
    CPU rlimit). The whole process tree is now killed."""
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'while True: pass'], start_new_session=True)\n"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(60)\n"
    )
    r = run_in_sandbox(
        {},
        root=tmp_path,
        timeout=1.5,
        policy="tests",
        command=[sys.executable, "-c", code],
        use_os_isolation=False,
    )
    # the detached child inherits our stdout pipe, so a missed kill also makes the call hang
    assert r.timed_out and pidfile.exists() and r.elapsed < 1.5 + 1.0, f"{r.elapsed:.2f}s"
    pid = int(pidfile.read_text())
    deadline = time.time() + 1.0
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    still = _alive(pid)
    if still:
        os.kill(pid, 9)  # do not leak it from the test run
    assert not still, "detached child survived the sandbox timeout"


def test_kill_group_refuses_a_reused_pid() -> None:
    """If the leader was reaped and its pid now belongs to an unrelated process, we must not
    SIGKILL that process's group (old code killed `getpgid(pid)` blindly)."""
    other = subprocess.Popen(["sleep", "30"])  # NOT a group leader: pid != its pgid
    try:
        sandbox._kill_group(other.pid)
        time.sleep(0.2)
        assert other.poll() is None, "killed an unrelated process"
    finally:
        other.kill()
        other.wait()


def test_cleanup_failures_cannot_hang_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def slow_run(*a: object, **k: object) -> None:
        calls.append(list(a[0]))  # type: ignore[arg-type]
        assert k.get("timeout") is not None, "docker command without a timeout"

    monkeypatch.setattr(subprocess, "run", slow_run)
    sandbox._docker(["rm", "-f", "x"], 30)
    assert calls == [["docker", "rm", "-f", "x"]]


@pytest.mark.skipif(not sandbox.docker_available(), reason="docker + image not available")
def test_docker_container_is_killed_at_timeout_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container runs the code, so it must be killed at the deadline, not merely the docker
    client (which used to leave the loop running until cleanup)."""
    state: dict[str, str] = {}
    real = sandbox._docker

    def spy(args: list[str], timeout: float) -> None:
        if args[0] == "rm":
            state["running"] = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", args[-1]],
                capture_output=True,
                text=True,
            ).stdout.strip()
        real(args, timeout)

    monkeypatch.setattr(sandbox, "_docker", spy)
    r = run_code(LOOP, timeout=2.0)
    assert r.isolation == "docker" and r.timed_out
    assert state.get("running") == "false", state


@pytest.mark.skipif(not sandbox.docker_available(), reason="docker + image not available")
def test_orphan_container_reaper() -> None:
    name = "cdebt-999999-1"  # pid 999999 is not a live process
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(
        ["docker", "create", "--name", name, sandbox.DOCKER_IMAGE, "true"],
        capture_output=True,
        check=True,
    )
    sandbox._REAPED = False
    sandbox._reap_orphan_containers()
    gone = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "-q"], capture_output=True, text=True
    ).stdout.strip()
    assert gone == ""
