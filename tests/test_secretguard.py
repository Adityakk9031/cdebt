"""Secrets never reach the sandbox or a quiz record. Fake secrets are assembled at runtime so that
no real-looking credential is written into this file (and push protection is not triggered)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdebt.agents.probes import execute, parse_proposal
from cdebt.config import Config
from cdebt.probes.base import copy_tree
from cdebt.quiz import build_quiz
from cdebt.secretguard import is_secret_file, looks_secret
from conftest import Repo

STRIPE = "sk" + "_live_" + "51HxYzA9" * 3
GITHUB = "gh" + "p_" + "a1B2c3D4" * 5
AWS = "AK" + "IA" + "Q3EXAMPLE7KEY2ZZ"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkw" + "." + "SflKxwRJSMeKKF2QT4fw"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
RANDOM = "Zm9vYmFyQmF6UXV4" + "MTIzNDU2Nzg5MGFi" + "Y2RlZmdo"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/prod.env",
        ".env.local",
        "certs/server.pem",
        "deploy/id_rsa",
        "keys/app.key",
        "secrets.yaml",
        "config/app-secrets.json",
        "service-account-prod.json",
        ".aws/credentials",
        "home/.ssh/config",
        ".npmrc",
        "infra/prod.tfvars",
    ],
)
def test_secret_files_are_recognised(path: str) -> None:
    assert is_secret_file(path)


@pytest.mark.parametrize(
    "path",
    ["pkg/pricing.py", "README.md", "config/settings.py", "tests/test_env.py", "docs/keys.md"],
)
def test_ordinary_files_are_not(path: str) -> None:
    assert not is_secret_file(path)


def test_secret_files_are_never_copied_into_the_sandbox(make_repo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("pkg/a.py", "X = 1\n")
    r.write("certs/server.pem", PEM + "\n")
    r.write("secrets.yaml", "token: x\n")
    r.commit("committed by mistake")
    r.write(".env", f"STRIPE_KEY={STRIPE}\n")  # untracked and NOT in .gitignore: git would copy it
    r.write(".aws/credentials", "[default]\n")
    dest = tmp_path / "sandbox"
    skipped = copy_tree(r.path, dest)
    assert (dest / "pkg" / "a.py").exists()
    for rel in (".env", "certs/server.pem", "secrets.yaml", ".aws/credentials"):
        assert not (dest / rel).exists(), rel
        assert rel in skipped
    assert not any(STRIPE in p.read_text(errors="ignore") for p in dest.rglob("*") if p.is_file())


@pytest.mark.parametrize("text", [STRIPE, GITHUB, AWS, JWT, PEM, RANDOM,
    "postgres://app:" + "hunter2pass" + "@db:5432/prod", "{'password': '" + "hunter2pass" + "'}"])  # fmt: skip
def test_secret_values_are_recognised(text: str) -> None:
    assert looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "12",
        "'pos'",
        "raises ValueError",
        "[1, 2, 3]",
        "{'rate': 10, 'name': 'standard'}",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # sha256 hex digest
        "550e8400-e29b-41d4-a716-446655440000",  # uuid
        "What does `late_fee(20, 14)` evaluate to?",
    ],
)
def test_ordinary_answers_are_not(text: str) -> None:
    assert not looks_secret(text)


@pytest.mark.posix_sandbox
def test_a_secret_constant_is_never_asked_about(make_repo) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    r.write("m.py", f"API_KEY = {STRIPE!r}\nRATE = 8 * 2\n")
    r.commit("m")
    probes, _units, skips = build_quiz(r.path, "m.py", Config(), n=10, kinds=("constant",))
    names = {p.evidence.get("name") for p in probes}
    assert "RATE" in names and "API_KEY" not in names
    assert any("looked like a secret" in s for s in skips)
    assert not any(STRIPE in (p.answer + " ".join(p.options)) for p in probes)


@pytest.mark.posix_sandbox
def test_an_agent_harness_cannot_read_an_unignored_env_file(make_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import cdebt.secretguard as guard
    from cdebt.secretguard import drop_secret_probes

    r: Repo = make_repo()
    r.write("m.py", "def f(x: int) -> int:\n    return x\n")
    r.commit("m")
    r.write(".env", f"STRIPE_KEY={STRIPE}\n")  # untracked and NOT in .gitignore
    harness = "def probe():\n    target_module.f(1)\n    return open('.env').read()\n"
    prop = parse_proposal(
        {"target": "f", "kind": "return_value", "question": "read env", "harness_code": harness}
    )
    # layer 1: the file is not in the sandbox at all
    p = execute(r.path, "m.py", prop, Config(), proposer="agent:test")
    assert p.answer == "raises FileNotFoundError" and STRIPE not in p.answer
    # layer 2: had the file been copied (as before this fix), the secret would have become the
    # answer; the answer filter drops such a question so it can never be stored or pushed
    monkeypatch.setattr(guard, "is_secret_file", lambda rel: False)
    leaked = execute(r.path, "m.py", prop, Config(), proposer="agent:test")
    assert STRIPE in leaked.answer  # the old behaviour: a real leak path
    kept, dropped = drop_secret_probes([leaked])
    assert kept == [] and dropped == 1
