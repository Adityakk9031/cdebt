from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cdebt.models import Record
from cdebt.store import Store
from cdebt.units import extract_units, unit_for_line
from conftest import Repo

SRC = "import os\n\ndef f(x):\n    return x + 1\n\nclass C:\n    def m(self):\n        return 2\n"


def test_units_partition_and_hash() -> None:
    us = extract_units("a.py", SRC)
    assert {u.symbol for u in us} == {"f", "C.m", "<module>"}
    covered = sorted(n for u in us for n in u.lines())
    assert covered == list(range(1, 9))
    assert unit_for_line(us, 4) is not None and unit_for_line(us, 4).symbol == "f"  # type: ignore[union-attr]


def test_hash_ignores_trailing_ws_not_code() -> None:
    a = {u.symbol: u for u in extract_units("a.py", SRC)}
    b = {u.symbol: u for u in extract_units("a.py", SRC.replace("x + 1", "x + 1   "))}
    c = {u.symbol: u for u in extract_units("a.py", SRC.replace("x + 1", "x + 2"))}
    assert a["f"].content_hash == b["f"].content_hash
    assert a["f"].content_hash != c["f"].content_hash
    assert a["C.m"].content_hash == c["C.m"].content_hash  # editing f doesn't touch C.m


def test_typescript_units() -> None:
    ts = (
        "export function a(): number { return 1 }\nconst b = (x: number) => x\n"
        "class K { m() { return 1 } }\n"
    )
    assert {u.symbol for u in extract_units("a.ts", ts)} >= {"a", "b", "K.m"}


def test_expiry_on_edit(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    st = Store(r.path)
    u = {x.symbol: x for x in extract_units("a.py", SRC)}["f"]
    assert st.status(u) == "none"
    st.add(Record(unit_path="a.py", unit_symbol="f", content_hash=u.content_hash, passed=True))
    st.save()
    assert Store(r.path).status(u) == "valid"
    edited = {x.symbol: x for x in extract_units("a.py", SRC.replace("x + 1", "x + 2"))}["f"]
    assert Store(r.path).status(edited) == "stale"  # code changed -> understanding expired
    assert len(Store(r.path).records) == 1  # kept for audit


def test_time_expiry_and_failed_record(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    st = Store(r.path)
    u = extract_units("a.py", SRC)[0]
    past = datetime.now(UTC) - timedelta(days=1)
    st.add(
        Record(
            unit_path=u.path,
            unit_symbol=u.symbol,
            content_hash=u.content_hash,
            passed=True,
            expires_at=past,
        )
    )
    assert st.status(u) == "stale"
    st.add(
        Record(unit_path=u.path, unit_symbol=u.symbol, content_hash=u.content_hash, passed=False)
    )
    assert not st.is_understood(u)


def test_store_writes_only_inside_cdebt_dir(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("a.py", SRC)
    r.commit("x")

    def snapshot() -> dict[str, str]:
        return {
            str(p.relative_to(r.path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in r.path.rglob("*")
            if p.is_file()
            and ".git/" not in str(p)
            and not str(p.relative_to(r.path)).startswith(".cdebt")
        }

    before, head = snapshot(), r.git("rev-parse", "HEAD")
    st = Store(r.path)
    st.add(Record(unit_path="a.py", unit_symbol="f", content_hash="h", passed=True))
    st.save()
    assert snapshot() == before and r.git("rev-parse", "HEAD") == head
    assert r.git("notes", "list") == "" and (r.path / ".cdebt" / "understanding.json").exists()
    assert list(Path(r.path / ".cdebt").glob(".understanding.*")) == []
