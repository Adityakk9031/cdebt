"""Fuzz the Node sandbox with random hostile JS payloads (same invariants as the Python fuzz)."""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cdebt.probes.js import node_ok
from test_sandbox_fuzz import FILE_SECRET, MARGIN, SEED, TIMEOUT, N, Res, World, _modes

pytestmark = pytest.mark.skipif(not node_ok()[0], reason="node >= 24 not installed")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CDEBT_CANARY_SECRET", "ENV-CANARY-77be02")
    w = World(tmp_path)
    yield w
    w.close()


def run_js(code: str, mode: str) -> Res:
    p = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_fuzz_worker.py"))],
        input=json.dumps({"code": code, "mode": mode, "timeout": TIMEOUT, "runtime": "node"}),
        capture_output=True,
        text=True,
        timeout=TIMEOUT + 60,
    )
    if p.returncode != 0:
        return Res({}, f"worker died (rc={p.returncode}): {p.stderr[-300:]}")
    return Res(json.loads(p.stdout.strip().splitlines()[-1]), None)


def payloads(rng: random.Random, w: World) -> str:
    c, v, d = json.dumps(str(w.canary)), json.dumps(str(w.victim)), json.dumps(str(w.dir))
    m = json.dumps(f"{tempfile.gettempdir()}/{w.tmp_marker}-js")
    p, s = w.tcp_port, json.dumps(w.unix_path)
    return rng.choice(
        [
            f"import fs from 'node:fs';\nfs.writeFileSync({v}, 'x');\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.appendFileSync({d} + '/new.txt', 'x');\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.unlinkSync({v});\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.renameSync({v}, {m});\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.mkdirSync({m});\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.symlinkSync('/etc/hosts', {m});\nexport const R = 1;",
            f"import fs from 'node:fs';\nfs.rmSync({d}, {{recursive: true}});\nexport const R = 1;",
            f"import fs from 'node:fs';\nexport const R = fs.readFileSync({c}, 'utf8');",
            f"import fs from 'node:fs';\nexport const R = fs.readdirSync({d}).join(',');",
            f"import fs from 'node:fs/promises';\nexport const R = await fs.readFile({c}, 'utf8');",
            "import fs from 'node:fs';\nexport const R = fs.readFileSync('/etc/hosts', 'utf8');",
            "import fs from 'node:fs';\nexport const R = fs.readFileSync('/proc/self/environ', 'utf8');",
            f"import net from 'node:net';\nnet.connect({p}, '127.0.0.1');\nexport const R = 1;",
            f"import net from 'node:net';\nnet.createConnection({s});\nexport const R = 1;",
            f"import http from 'node:http';\nhttp.get('http://127.0.0.1:{p}/');\nexport const R = 1;",
            f"export const R = await fetch('http://127.0.0.1:{p}/').then(r => r.status);",
            f"import dgram from 'node:dgram';\ndgram.createSocket('udp4').send('x', {w.udp_port}, '127.0.0.1');\nexport const R = 1;",
            "import dns from 'node:dns';\ndns.lookup('example.com', () => {});\nexport const R = 1;",
            f"import cp from 'node:child_process';\ncp.execSync('touch ' + {m});\nexport const R = 1;",
            f"import cp from 'node:child_process';\ncp.spawn('touch', [{m}]);\nexport const R = 1;",
            "import { Worker } from 'node:worker_threads';\nnew Worker('1', {eval: true});\nexport const R = 1;",
            f"process.kill({w.sentinel.pid}, 9);\nexport const R = 1;",
            "process.kill(process.ppid, 9);\nexport const R = 1;",
            "process.abort();\nexport const R = 1;",
            "export const R = JSON.stringify(process.env);",
            "export const R = JSON.stringify(Object.entries(process.env));",
            "while (true) {}\nexport const R = 1;",
            "const a = [];\nwhile (true) a.push(new Array(1e6).fill(1));\nexport const R = 1;",
            "for (;;) process.stdout.write('x'.repeat(1e6));\nexport const R = 1;",
            "export const R = await new Promise(() => {});",
            "function f(n) { return f(n + 1) + 1; }\nexport const R = f(0);",
        ]
    )


def test_node_fuzz(world: World) -> None:
    rng = random.Random(f"{SEED}-node")
    modes = _modes()
    for i in range(max(N * 3, 12)):
        code = payloads(rng, world)
        mode = modes[i % len(modes)]
        res = run_js(code, mode)
        world.check(res, f"[node/{mode}/#{i}] {code!r}")
        assert FILE_SECRET not in (res.stdout + res.payload)
        assert res.elapsed < TIMEOUT + MARGIN
