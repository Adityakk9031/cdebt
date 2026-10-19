"""Node sandbox: each hostile payload must be contained (permission model + disabled network
entry points + OS layer). Skipped when Node >= 24 is not installed."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from cdebt.probes.js import node_ok
from cdebt.sandbox import run_in_sandbox

pytestmark = pytest.mark.skipif(not node_ok()[0], reason="node >= 24 not installed")


def run_payload(
    tmp_path: Path, body: str, timeout: float = 8.0, mem_mb: int = 256, iso: bool = True
):  # type: ignore[no-untyped-def]
    root = tmp_path.resolve()
    (root / "payload.mjs").write_text(body)
    return run_in_sandbox(
        {"mode": "attrs", "module": str(root / "payload.mjs"), "names": ["R"]},
        root=root,
        timeout=timeout,
        mem_mb=mem_mb,
        runtime="node",
        use_os_isolation=iso,
    )


def test_clean_module_value_is_returned(tmp_path: Path) -> None:
    r = run_payload(tmp_path, "export const R = [1, 'a', {k: 2}];")
    assert r.payload and r.payload["values"] == {"R": ['[1, "a", {"k": 2}]', '[1, "a", {"k": 2}]']}
    assert not r.violations


def test_file_write_blocked(tmp_path: Path) -> None:
    victim = tmp_path.parent / "node_victim.txt"
    victim.unlink(missing_ok=True)
    r = run_payload(
        tmp_path,
        f"import fs from 'node:fs';\nfs.writeFileSync({str(victim)!r}, 'x');\nexport const R = 1;",
    )
    assert not victim.exists()
    assert "restricted" in str((r.payload or {}).get("error", ""))


def test_write_inside_root_also_blocked(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "import fs from 'node:fs';\nfs.writeFileSync('in_root.txt', 'x');\nexport const R = 1;",
    )
    assert not (tmp_path / "in_root.txt").exists() and "error" in (r.payload or {})


def test_read_outside_root_blocked(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "import fs from 'node:fs';\nexport const R = fs.readFileSync('/etc/hosts', 'utf8').length;",
    )
    assert "restricted" in str((r.payload or {}).get("error", ""))


def test_child_process_blocked(tmp_path: Path) -> None:
    marker = tmp_path.parent / "node_marker"
    marker.unlink(missing_ok=True)
    r = run_payload(
        tmp_path,
        f"import cp from 'node:child_process';\ncp.execSync('touch {marker}');\nexport const R = 1;",
    )
    assert not marker.exists() and "restricted" in str((r.payload or {}).get("error", ""))


def test_worker_threads_blocked(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "import { Worker } from 'node:worker_threads';\nnew Worker('1', {eval: true});\nexport const R = 1;",
    )
    assert "error" in (r.payload or {})


@pytest.mark.parametrize("iso", [False, True])
def test_network_blocked(tmp_path: Path, iso: bool) -> None:
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
    body = (
        "import net from 'node:net';\nimport http from 'node:http';\n"
        f"let n = 0;\ntry {{ net.connect({port}, '127.0.0.1'); }} catch (e) {{ n++; }}\n"
        f"try {{ http.get('http://127.0.0.1:{port}/'); }} catch (e) {{ n++; }}\n"
        f"try {{ await fetch('http://127.0.0.1:{port}/'); }} catch (e) {{ n++; }}\n"
        "export const R = n;"
    )
    r = run_payload(tmp_path, body, iso=iso)
    srv.close()
    assert not hits, "a connection reached the local server"
    assert r.payload and r.payload["values"]["R"][0] == "3" and len(r.violations) >= 2


def test_dns_blocked(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "import dns from 'node:dns';\ntry { dns.lookup('example.com', () => {}); } catch (e) {}\nexport const R = 1;",
    )
    assert any("lookup" in v for v in r.violations)


def test_env_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDEBT_CANARY_SECRET", "s3cr3t-canary")
    r = run_payload(tmp_path, "export const R = JSON.stringify(process.env);")
    text = json.dumps(r.payload)
    assert "s3cr3t-canary" not in text and "CDEBT_CANARY" not in text


def test_infinite_loop_killed(tmp_path: Path) -> None:
    r = run_payload(tmp_path, "while (true) {}\nexport const R = 1;", timeout=1.5)
    assert r.timed_out and r.elapsed < 1.5 + 1.5


def test_memory_bomb_contained(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "const a = [];\nwhile (true) a.push(new Array(1e6).fill(1));\nexport const R = 1;",
        timeout=20,
        mem_mb=200,
    )
    assert (
        r.returncode != 0 or r.killed_for_memory or "error" in (r.payload or {})
    ) and r.elapsed < 20


def test_stdout_flood_does_not_break_parent(tmp_path: Path) -> None:
    r = run_payload(
        tmp_path,
        "for (let i = 0; i < 200000; i++) process.stdout.write('x'.repeat(100));\nexport const R = 1;",
        timeout=10,
    )
    assert r.elapsed < 10 and os.getpid()
