"""Behind the scenes: what cdebt does AFTER you answer a question. Safe to run on camera.

Run it inside the demo project (after `setup_demo.sh`):

    python /path/to/cdebt/examples/first_user/sandbox_peek.py

Part 1 runs one of your real functions in the sandbox and shows the proof cdebt keeps.
Part 2 shows the same sandbox refusing hostile behaviour. Every target is harmless: a FAKE secret
file, a temp file, a local port and a `sleep` process, all created here, and each is checked from
OUTSIDE the sandbox afterwards. Honest limit: this is containment against accidental and
AI-written side effects, not a defence against a determined attacker with native-code exploits.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

from cdebt import sandbox
from cdebt.agents.probes import execute, parse_proposal
from cdebt.config import load_config
from cdebt.sandbox import run_code

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"
sandbox.set_default_isolation("strong")  # the default for untrusted code: Docker, or strict macOS


def pause() -> None:
    if os.environ.get("PEEK_PAUSE"):
        input(f"{D}(press Enter){X}")


repo = Path(".").resolve()
target = repo / "pkg" / "pricing.py"
if not target.exists():
    raise SystemExit("run this inside the demo project (it needs pkg/pricing.py)")

print(f"\n{B}PART 1. Your real function runs in a throw-away sandbox{X}\n")
src = target.read_text()
print(f"{D}function under test:{X}")
for ln in src.split("def late_fee")[1].split("\n\n")[0].splitlines()[:7]:
    print("  " + ("def late_fee" + ln if ln.startswith("(") else ln))
print()
prop = parse_proposal(
    {"target": "late_fee", "kind": "return_value", "call_inputs": {"args": [20, 14]}}
)
probe = execute(repo, "pkg/pricing.py", prop, load_config(repo), proposer="peek")
ev = probe.evidence
print(f"  question : {probe.question}")
print(f"  {G}true answer: {probe.answer}{X}  (computed by RUNNING it, never by a model)")
print(f"  where it ran       : {ev.get('isolation')}")
print(f"  runs, same result  : {ev.get('deterministic_runs')}  (a flaky function is rejected)")
print(f"  lines that executed: {ev.get('executed_lines')}")
print(f"  verified by        : {probe.verified_by}")
pause()

print(f"\n{B}PART 2. The same sandbox refuses hostile behaviour{X}\n")
tmp = Path(tempfile.mkdtemp(prefix="cdebt-peek-"))
secret = tmp / "FAKE_secret.txt"
secret.write_text("FAKE-SECRET-12345")
victim = tmp / "victim.txt"
victim.write_text("original")
outside = tmp / "should-not-exist.txt"
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
hits: list[int] = []


def _listen() -> None:
    try:
        srv.accept()
        hits.append(1)
    except OSError:  # the socket is closed at the end of the demo
        pass


threading.Thread(target=_listen, daemon=True).start()
sentinel = subprocess.Popen(["sleep", "300"])

attacks = [
    ("read a secret file outside the sandbox", f"print(open({str(secret)!r}).read())"),
    ("overwrite a file outside the sandbox", f"open({str(victim)!r}, 'w').write('pwned')"),
    ("create a new file outside the sandbox", f"open({str(outside)!r}, 'w').write('x')"),
    (
        "open a network connection",
        f"import socket; socket.create_connection(('127.0.0.1', {srv.getsockname()[1]}))",
    ),
    ("kill another process", f"import os; os.kill({sentinel.pid}, 9)"),
    ("run a shell command", "import os; os.system('echo pwned')"),
]
leaked = False
for name, code in attacks:
    r = run_code(code)
    leaked = leaked or "FAKE-SECRET" in repr(r)
    verdict = f"{G}BLOCKED{X}" if r.violations else f"{R}ALLOWED?{X}"
    why = (r.violations[0] if r.violations else str((r.payload or {}).get("error", ""))[:70])[:70]
    print(f"  {verdict}  {name:<40} {D}{why}{X}")


def check(label: str, ok: bool) -> None:
    print(f"  {label:<30}: {(G if ok else R)}{ok}{X}")


print(f"\n{B}Checked from OUTSIDE the sandbox:{X}")
check("secret never came out", not leaked)
check("victim file unchanged", victim.read_text() == "original")
check("new file was not created", not outside.exists())
check("nobody connected to the port", not hits)
check("the other process is alive", sentinel.poll() is None)
print(f"\n{Y}Honest limit:{X} containment against accidental and AI-written side effects,")
print("not a defence against a determined attacker. It has not had an independent review.\n")

sentinel.kill()
srv.close()
for f in (secret, victim, outside):
    f.unlink(missing_ok=True)
tmp.rmdir()
