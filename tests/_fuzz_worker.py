"""Runs ONE sandbox payload in a disposable process and prints the result as JSON. The fuzz
harness uses this so a payload that escapes far enough to kill its parent kills only this worker
(which the harness then reports as a finding) instead of the test runner."""

from __future__ import annotations

import json
import sys

from cdebt import sandbox

spec = json.load(sys.stdin)
mode = spec["mode"]
if spec.get("runtime") == "node":
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp(prefix="cdebt-fz-")).resolve()
    (d / "payload.mjs").write_text(spec["code"])
    res = sandbox.run_in_sandbox(
        {"mode": "attrs", "module": str(d / "payload.mjs"), "names": ["R"]},
        root=d,
        timeout=spec["timeout"],
        use_os_isolation=mode != "audit",
        isolation="strong" if mode == "strong" else "host",
        runtime="node",
        sanitize=spec.get("sanitize", True),
    )
elif spec.get("root"):
    from pathlib import Path

    res = sandbox.run_in_sandbox(
        {"mode": "exec", "code": spec["code"]},
        root=Path(spec["root"]),
        timeout=spec["timeout"],
        use_os_isolation=mode != "audit",
        isolation="strong" if mode == "strong" else "host",
        sanitize=spec.get("sanitize", True),
    )
else:
    res = sandbox.run_code(
        spec["code"],
        timeout=spec["timeout"],
        os_isolation=mode != "audit",
        isolation="strong" if mode == "strong" else "host",
    )
print(
    json.dumps(
        {
            "returncode": res.returncode,
            "stdout": res.stdout[-20000:],
            "stderr": res.stderr[-4000:],
            "timed_out": res.timed_out,
            "killed_for_output": res.killed_for_output,
            "killed_for_memory": res.killed_for_memory,
            "violations": res.violations,
            "elapsed": res.elapsed,
            "isolation": res.isolation,
            "payload": str(res.payload)[:20000],
            "stdout_len": len(res.stdout),
        }
    )
)
