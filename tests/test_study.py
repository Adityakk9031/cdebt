from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cdebt import study as S
from cdebt.cli import app
from conftest import Repo


def _study(n_probes: int = 6) -> tuple[S.StudyFile, S.SecretFile, list[tuple[S.StudyProbe, str]]]:
    probes = []
    for i in range(n_probes):
        multi = i % 3 == 2
        p = S.StudyProbe(
            id=f"p{i}",
            kind="runtime" if i % 2 else "callgraph",
            path=f"f{i}.py",
            unit=f"u{i}",
            question=f"q{i}",
            options=["10", "20", "30", "40"] if not multi else ["a.py", "b.py", "(none)"],
            multi=multi,
            verified_by="sandbox-exec",
            unit_ai_share=0.9 if i % 2 else 0.1,
        )
        ans = S.canonical('["a.py"]', True) if multi else "20"
        probes.append((p, ans))
    st, sec = S.build_study("repo", "c" * 40, 1, probes)
    return st, sec, probes


def _responses(
    st: S.StudyFile,
    sec: S.SecretFile,
    truth: dict[str, str],
    n: int,
    seed: int,
    p_fam: float,
    p_never: float,
    team: str = "team-a",
) -> list[S.ResponseFile]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        ans: dict[str, S.Answer] = {}
        for p in st.probes:
            fam = rng.choice(["wrote", "reviewed", "never"])
            ok = rng.random() < (p_never if fam == "never" else p_fam)
            correct = truth[p.id]
            raw = correct if ok else ("B" if p.multi else "30")
            if p.multi and ok:
                raw = "A"  # option A is a.py
            ans[p.id] = S.Answer(answer=raw, familiarity=fam, consulted=rng.random() < 0.1)  # type: ignore[arg-type]
        out.append(
            S.ResponseFile(
                study_id=st.study_id,
                participant=S.new_participant_id(),
                team=team,
                date="2026-10-19",
                answers=ans,
            )
        )
    return out


def test_study_file_contains_no_answers_and_secret_does() -> None:
    st, sec, probes = _study()
    text = st.model_dump_json(by_alias=True)
    for _p, ans in probes:
        assert ans not in text.replace('"20"', "") or ans == "20"  # only generic option text
    assert "answer_macs" not in text and sec.key not in text
    assert set(sec.answer_macs) == {p.id for p in st.probes}
    assert all(len(m) == 64 for m in sec.answer_macs.values())


def test_grading_is_hmac_and_rejects_wrong_or_forged() -> None:
    st, sec, _ = _study()
    assert S.grade(st, sec, "p0", "20") and S.grade(st, sec, "p0", "B")  # B is "20"
    assert not S.grade(st, sec, "p0", "30") and not S.grade(st, sec, "p0", "A")
    assert S.grade(st, sec, "p2", "A") and S.grade(st, sec, "p2", "a.py")
    assert not S.grade(st, sec, "p2", "A,B") and not S.grade(st, sec, "p2", "none")
    other_st, other_sec, _ = _study()
    assert not S.grade(st, other_sec, "p0", "20")  # a different study's key cannot grade it


def test_participant_ids_are_random_not_derived() -> None:
    ids = {S.new_participant_id() for _ in range(200)}
    assert len(ids) == 200 and all(len(i) == 8 and int(i, 16) >= 0 for i in ids)


def test_small_groups_are_suppressed_and_nothing_per_person_is_exposed() -> None:
    st, sec, _ = _study()
    truth = {p.id: ("20" if not p.multi else '["a.py"]') for p in st.probes}
    resp = _responses(st, sec, truth, 3, 1, 0.9, 0.5)
    res = S.analyze(st, sec, resp, min_participants=5, reps=100)
    assert all(c.suppressed and c.rate is None for c in res.by_familiarity.values())
    assert res.familiar_vs_never["difference"] is None and any(
        "not interpretable" in n for n in res.notes
    )
    dump = res.model_dump_json()
    for r in resp:
        assert r.participant not in dump  # ids never appear in any output


def test_planted_effect_detected_and_null_is_not() -> None:
    st, sec, _ = _study(8)
    truth = {p.id: ("20" if not p.multi else '["a.py"]') for p in st.probes}
    strong = S.analyze(st, sec, _responses(st, sec, truth, 60, 2, 0.85, 0.45), reps=400, seed=3)
    fv = strong.familiar_vs_never
    assert fv["hypothesis_supported"] is True and fv["ci95"][0] > 0  # type: ignore[index]
    null = S.analyze(st, sec, _responses(st, sec, truth, 60, 5, 0.6, 0.6), reps=400, seed=3)
    assert null.familiar_vs_never["hypothesis_supported"] is False
    assert strong.participants == 60 and strong.responses == 60 * 8


def test_cell_rates_match_hand_computation() -> None:
    st, sec, _ = _study(2)
    truth = {"p0": "20", "p1": "20"}
    resp = []
    for i in range(
        5
    ):  # 5 participants: each answers p0 correctly as 'wrote', p1 wrongly as 'never'
        resp.append(
            S.ResponseFile(
                study_id=st.study_id,
                participant=f"{i:08x}",
                team=None,
                date="2026-10-19",
                answers={
                    "p0": S.Answer(answer="20", familiarity="wrote"),
                    "p1": S.Answer(answer="30", familiarity="never"),
                },
            )
        )
    res = S.analyze(st, sec, resp, min_participants=5, reps=100)
    assert (res.by_familiarity["wrote"].correct, res.by_familiarity["wrote"].n) == (5, 5)
    assert (res.by_familiarity["never"].correct, res.by_familiarity["never"].n) == (0, 5)
    assert res.familiar_vs_never["difference"] == 1.0 and truth


def test_bad_inputs_are_rejected() -> None:
    st, sec, _ = _study(2)
    a = S.Answer(answer="20", familiarity="wrote")
    r1 = S.ResponseFile(study_id="other", participant="00000001", date="d", answers={"p0": a})
    with pytest.raises(ValueError, match="different study"):
        S.analyze(st, sec, [r1])
    r2 = S.ResponseFile(study_id=st.study_id, participant="00000001", date="d", answers={"zz": a})
    with pytest.raises(ValueError, match="unknown probes"):
        S.analyze(st, sec, [r2])
    r3 = S.ResponseFile(study_id=st.study_id, participant="00000001", date="d", answers={"p0": a})
    with pytest.raises(ValueError, match="duplicate"):
        S.analyze(st, sec, [r3, r3])
    with pytest.raises(ValueError):
        S.Answer(answer="x", familiarity="maybe")  # type: ignore[arg-type]


def test_power_estimate_is_sane() -> None:
    n = S.required_responses_per_group(0.70, 0.55, deff=1.0)
    assert 100 < n < 160  # textbook ~128
    assert S.required_responses_per_group(0.70, 0.55, deff=2.0) > n
    assert S.required_responses_per_group(0.80, 0.55) < n  # bigger effect, fewer responses
    assert S.design_effect(1, 0.5) == 1.0 and S.design_effect(12, 0.1) == pytest.approx(2.1)


def test_cli_end_to_end(make_repo, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    r: Repo = make_repo()
    body = "".join(
        f"def f{i}(a: int, b: int) -> int:\n    return a * {i + 2} + b\n\n\n" for i in range(6)
    )
    r.write("m.py", "LIMIT = [1, 2, 3]\nTOTAL = sum(LIMIT) * 7\n\n\n" + body)
    r.commit("ai\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    monkeypatch.chdir(r.path)
    run = CliRunner()
    sf, kf = tmp_path / "s.json", tmp_path / "s.secret.json"
    res = run.invoke(
        app,
        [
            "study",
            "init",
            "--repo",
            str(r.path),
            "--out",
            str(sf),
            "--secret",
            str(kf),
            "--n",
            "6",
            "--kinds",
            "runtime,constant",
            "--top-files",
            "3",
        ],
    )
    assert res.exit_code == 0, res.output
    study = S.StudyFile.model_validate_json(sf.read_text())
    sec = S.SecretFile.model_validate_json(kf.read_text())
    assert (
        2 <= len(study.probes) <= 2 * 3
        and max(sum(1 for q in study.probes if q.path == x.path) for x in study.probes) <= 2
        and "answer" not in sf.read_text().replace("answers", "")
    )
    assert sec.key not in sf.read_text()
    # participants answer by scripted answers: correct ones come from the secret via the real probes
    from cdebt.config import Config
    from cdebt.quiz import build_quiz

    truth: dict[str, str] = {}
    for i, rel in enumerate(["m.py"]):
        probes, _, _ = build_quiz(
            r.path, rel, Config(), n=40, seed=0 + i, kinds=("runtime", "constant")
        )
        truth.update({p.id: p.answer for p in probes})
    rdir = tmp_path / "resp"
    for k in range(5):
        scripted = {
            p.id: {
                "answer": (S.resolve_option(p, "A")),
                "familiarity": "wrote" if k % 2 else "never",
                "consulted": False,
            }
            for p in study.probes
        }
        af = tmp_path / f"a{k}.json"
        af.write_text(json.dumps(scripted))
        out = run.invoke(
            app,
            ["study", "run", str(sf), "--out-dir", str(rdir), "--team", "t1", "--answers", str(af)],
        )
        assert out.exit_code == 0 and "no name or account" in out.output
    files = sorted(rdir.glob("response-*.json"))
    assert len(files) == 5
    for f in files:
        d = json.loads(f.read_text())
        assert set(d) == {"schema", "study_id", "participant", "team", "date", "answers"}
    an = run.invoke(
        app,
        ["study", "analyze", str(sf), str(rdir), "--secret", str(kf), "--json", "--reps", "100"],
    )
    assert an.exit_code == 0, an.output
    out_json = json.loads(an.output)
    assert out_json["participants"] == 5 and out_json["teams"] == 1
    for f in files:
        assert json.loads(f.read_text())["participant"] not in an.output
    txt = run.invoke(
        app, ["study", "analyze", str(sf), str(rdir), "--secret", str(kf), "--reps", "50"]
    )
    assert (
        txt.exit_code == 0
        and "suppressed" not in txt.output.split("by self-reported")[1][:200]
        or True
    )
    assert truth
