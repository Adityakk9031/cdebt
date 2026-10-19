from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdebt.cli import app
from cdebt.models import Record
from cdebt.store import CURRENT_SCHEMA, MIGRATIONS, Store, StoreError, migrate, parse_document
from cdebt.units import extract_units
from conftest import FIX, Repo

V1 = FIX / "store" / "understanding.v1.json"


def _put(repo: Path, text: str) -> Path:
    f = repo / ".cdebt" / "understanding.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def test_current_schema_is_2_and_every_step_has_a_migration() -> None:
    assert CURRENT_SCHEMA == 2
    assert sorted(MIGRATIONS) == list(range(1, CURRENT_SCHEMA))


def test_v1_file_loads_and_records_become_verified(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    _put(r.path, V1.read_text())
    st = Store(r.path)
    assert st.migrated_from == 1 and st.needs_migration()
    recs = st.records
    assert [x.unit_symbol for x in recs] == ["add", "shout"]
    assert all(x.kind == "verified" and x.note is None and x.scope is None for x in recs)
    assert recs[0].probes[0].verified_by == "sandbox-exec" and recs[0].audit_label == "legacy-audit"
    assert recs[1].passed is False


def test_missing_schema_version_is_treated_as_v1() -> None:
    data, found = migrate({"records": []})
    assert found == 1 and data["schema_version"] == 2


def test_v1_hash_binding_still_works_after_migration(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    src = "def add(a: int, b: int) -> int:\n    return a + b\n"
    u = extract_units("pkg/mathy.py", src)[0]
    doc = json.loads(V1.read_text())
    doc["records"][0]["content_hash"] = u.content_hash
    _put(r.path, json.dumps(doc))
    st = Store(r.path)
    assert st.status(u) == "verified"
    changed = extract_units("pkg/mathy.py", src.replace("a + b", "a - b"))[0]
    assert st.status(changed) == "stale"


def test_save_writes_current_schema_and_one_backup(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    f = _put(r.path, V1.read_text())
    original = f.read_bytes()
    st = Store(r.path)
    st.add(
        Record(
            unit_path="a.py",
            unit_symbol="f",
            content_hash="h",
            passed=True,
            kind="attested",
            note="read it",
            scope="module",
        )
    )
    st.save()
    on_disk = json.loads(f.read_text())
    assert on_disk["schema_version"] == 2 and len(on_disk["records"]) == 3
    assert all("kind" in x for x in on_disk["records"])
    bak = r.path / ".cdebt" / "understanding.v1.bak.json"
    assert bak.read_bytes() == original  # the pre-migration bytes, untouched
    st2 = Store(r.path)
    assert not st2.needs_migration()
    st2.save()  # saving a current-schema file must not create/overwrite backups
    assert bak.read_bytes() == original
    assert not (r.path / ".cdebt" / "understanding.v2.bak.json").exists()


def test_newer_schema_is_refused_not_guessed(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    _put(r.path, json.dumps({"schema_version": 99, "records": []}))
    with pytest.raises(StoreError, match="newer version"):
        Store(r.path)


@pytest.mark.parametrize(
    "text", ["{not json", "[]", '{"schema_version": 2, "records": [{"x": 1}]}']
)
def test_corrupt_files_give_a_clear_error(make_repo, text) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    _put(r.path, text)
    with pytest.raises(StoreError):
        Store(r.path)


def test_unknown_record_fields_survive_a_roundtrip(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    doc = {
        "schema_version": 2,
        "records": [
            {
                "unit_path": "a.py",
                "unit_symbol": "f",
                "content_hash": "h",
                "passed": True,
                "kind": "verified",
                "from_the_future": {"x": 1},
            }
        ],
    }
    _put(r.path, json.dumps(doc))
    st = Store(r.path)
    st.save()
    assert json.loads((r.path / ".cdebt" / "understanding.json").read_text())["records"][0][
        "from_the_future"
    ] == {"x": 1}


def test_at_rev_migrates_committed_v1(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    _put(r.path, V1.read_text())
    r.commit("v1 store")
    st = Store.at_rev(r.path, "HEAD")
    assert st.migrated_from == 1 and len(st.records) == 2 and st.records[0].kind == "verified"
    assert (r.path / ".cdebt" / "understanding.json").read_text() == V1.read_text()  # read-only


def test_cli_migrate_check_and_apply(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    monkeypatch.chdir(r.path)
    run = CliRunner()
    assert run.invoke(app, ["migrate", "--repo", str(r.path)]).exit_code == 0  # nothing there
    f = _put(r.path, V1.read_text())
    chk = run.invoke(app, ["migrate", "--repo", str(r.path), "--check"])
    assert chk.exit_code == 1 and "schema 1" in chk.output
    assert f.read_text() == V1.read_text()  # --check never writes
    done = run.invoke(app, ["migrate", "--repo", str(r.path)])
    assert done.exit_code == 0 and "1 -> 2" in done.output
    assert json.loads(f.read_text())["schema_version"] == 2
    assert run.invoke(app, ["migrate", "--repo", str(r.path), "--check"]).exit_code == 0


def test_cli_commands_report_corrupt_store_cleanly(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", "def f(a: int) -> int:\n    return a\n")
    r.commit("x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    _put(r.path, "{broken")
    res = CliRunner().invoke(app, ["map", "--repo", str(r.path), "--no-scip"])
    assert res.exit_code == 2 and "not valid JSON" in res.output and "Traceback" not in res.output
    _put(r.path, json.dumps({"schema_version": 99}))
    res = CliRunner().invoke(app, ["plan", "--repo", str(r.path), "--no-scip"])
    assert res.exit_code == 2 and "newer version" in res.output


def test_parse_document_roundtrip_is_stable() -> None:
    doc, found = parse_document(V1.read_text())
    again, found2 = parse_document(doc.model_dump_json())
    assert found == 1 and found2 == 2 and again.records == doc.records
