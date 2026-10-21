"""CI gate: fail when AI-attributed CHANGED lines sit in units nobody has understood.

Unknown attribution is reported, never silently gated; `strict_unknown` opts in to failing."""

from __future__ import annotations

import contextlib
from pathlib import Path

from cdebt import gitutil
from cdebt import notes as notes_mod
from cdebt import recheck as recheck_mod
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.config import Config
from cdebt.models import AuthorKind, CodeUnit, GateResult, Violation
from cdebt.store import Store
from cdebt.units import extract_units, unit_for_line


def run_gate(
    repo: Path,
    cfg: Config,
    base: str,
    head: str = "HEAD",
    *,
    max_ai_lines: int = 0,
    strict_unknown: bool = False,
    require: str = "default",
    store: Store | None = None,
    use_notes: bool = True,
    recheck: bool = False,
) -> GateResult:
    if require not in ("default", "verified", "attested"):
        raise ValueError(f"--require must be verified or attested, got {require!r}")
    head_sha = gitutil.resolve(repo, head)
    head_checked_out = head_sha == gitutil.resolve(repo, "HEAD")
    store = store or Store.at_rev(repo, head_sha, head_sha if head_checked_out else None)
    if use_notes:
        with contextlib.suppress(
            gitutil.GitError
        ):  # unresolvable base: committed file still counts
            store.extend_readonly(notes_mod.read_range(repo, base, head_sha))
    understood: list[Violation] = []
    failed_recheck: dict[tuple[str, str], str] = {}
    checked: dict[tuple[str, str], str | None] = {}
    providers = default_providers(repo, use_trailers=cfg.use_trailers)
    violations: list[Violation] = []
    attested_ok: list[Violation] = []
    unknown_files: list[str] = []
    unknown_lines = 0
    ai_unverified = 0
    sources: dict[str, int] = {}
    notes: list[str] = []
    changed_total = 0
    for h in gitutil.changed_lines(repo, base, head_sha):
        if not h.path.endswith(cfg.extensions):
            continue
        text = gitutil.show_file(repo, head_sha, h.path)
        if text is None:
            continue
        fa = resolve_file(repo, h.path, head_sha, providers)
        units = extract_units(h.path, text)
        src_lines = text.splitlines()
        per_unit: dict[str, list[int]] = {}
        att_unit: dict[str, list[int]] = {}
        understood_acc: dict[str, list[int]] = {}
        file_unknown = 0
        for ln in h.lines:
            if not 1 <= ln <= fa.total:
                continue
            changed_total += 1
            if not _has_code(src_lines[ln - 1] if ln <= len(src_lines) else ""):
                continue  # blank and comment-only lines carry no behaviour: nothing to understand
            kind = fa.kinds[ln - 1]
            if kind is AuthorKind.UNKNOWN and not cfg.unknown_as_ai:
                file_unknown += 1
                continue
            if kind is AuthorKind.HUMAN:
                continue
            sources[fa.sources[ln - 1].value] = sources.get(fa.sources[ln - 1].value, 0) + 1
            u = unit_for_line(units, ln)
            if u is None:
                continue
            verdict = _verdict(store, u, require)
            if verdict == "ok" and recheck:
                key = (h.path, u.symbol)
                if key not in checked:
                    rec = store.record_for(u)
                    rr = recheck_mod.verify_record(repo, h.path, rec, cfg, text) if rec else None
                    checked[key] = None if rr is None or rr.ok else (rr.reason or "recheck failed")
                if checked[key]:
                    failed_recheck[key] = checked[key] or ""
                    verdict = "violation"
            if verdict == "ok":
                understood_acc.setdefault(u.symbol, []).append(ln)
            elif verdict == "violation":
                per_unit.setdefault(u.symbol, []).append(ln)
            elif verdict == "attested":
                att_unit.setdefault(u.symbol, []).append(ln)
        if file_unknown:
            unknown_lines += file_unknown
            unknown_files.append(h.path)
        by_sym = {x.symbol: x for x in units}
        for sym, lns in understood_acc.items():
            understood.append(
                Violation(
                    path=h.path,
                    symbol=sym,
                    ai_lines=lns,
                    status="verified",
                    attribution_source=fa.source,
                    ranges=by_sym[sym].ranges,
                )
            )
        for sym, lns in att_unit.items():
            attested_ok.append(
                Violation(
                    path=h.path,
                    symbol=sym,
                    ai_lines=lns,
                    status="attested",
                    attribution_source=fa.source,
                    ranges=by_sym[sym].ranges,
                )
            )
        for sym, lns in per_unit.items():
            u = by_sym[sym]
            bad = failed_recheck.get((h.path, sym))
            st = store.status(u)
            violations.append(
                Violation(
                    path=h.path,
                    symbol=sym,
                    ai_lines=lns,
                    status="recheck_failed" if bad else st,
                    attribution_source=fa.source,
                    ranges=u.ranges,
                    reason=f"recheck_failed: {bad}" if bad else store.latest_reason(u),
                )
            )
            ai_unverified += len(lns)
    if unknown_lines and not sources:
        notes.append(
            "No attribution source covers the changed lines (all unknown). Nothing was "
            "gated on them; use --strict-unknown to fail on unknown attribution."
        )
    elif unknown_lines:
        notes.append(
            f"{unknown_lines} changed line(s) have unknown attribution and were not "
            "gated (unknown is not human-written)."
        )
    att_lines = sum(len(v.ai_lines) for v in attested_ok)
    if att_lines:
        notes.append(
            f"{att_lines} AI line(s) in {len(attested_ok)} unit(s) were accepted on ATTESTATION "
            "(a human sign-off; NON-VERIFIED, not execution-checked)."
        )
    rejected_att = sum(1 for v in violations if v.status == "attested")
    if rejected_att:
        notes.append(
            f"{rejected_att} unit(s) are attested (NON-VERIFIED) but attestation is not accepted "
            "for them under this policy: a function that `cdebt quiz` can verify needs "
            "verified understanding (or run the gate with --require attested)."
        )
    module_failing = sum(
        1 for v in violations if v.symbol.startswith("<") and v.status != "attested"
    )
    if module_failing:
        notes.append(
            f"{module_failing} failing unit(s) are module-level code: clear them with "
            "`cdebt quiz` (module constants) or `cdebt attest` (NON-VERIFIED)."
        )
    if any(s == "trailer" for s in sources):
        notes.append("Some attribution is from commit trailers: coarse, commit-level.")
    fail = ai_unverified > max_ai_lines or (strict_unknown and unknown_lines > 0)
    if strict_unknown and unknown_lines:
        notes.append("--strict-unknown: failing because of unknown attribution.")
    if changed_total == 0:
        notes.append("No changed source lines in range.")
    violations.sort(key=lambda v: (-len(v.ai_lines), v.path, v.symbol))
    return GateResult(
        passed=not fail,
        base=base,
        head=head_sha[:12],
        ai_lines_unverified=ai_unverified,
        max_ai_lines=max_ai_lines,
        strict_unknown=strict_unknown,
        require=require,
        attested_accepted=attested_ok,
        understood=understood,
        rechecked=recheck,
        attested_accepted_lines=att_lines,
        violations=violations,
        unknown_files=sorted(unknown_files),
        unknown_changed_lines=unknown_lines,
        attribution_sources=sources,
        notes=notes,
    )


def _has_code(line: str) -> bool:
    t = line.strip()
    return bool(t) and not t.startswith(("#", "//", "/*", "*"))


def _verdict(store: Store, u: CodeUnit, require: str) -> str:
    """ok | attested (accepted, NON-VERIFIED) | violation."""
    st = store.status(u)
    if st == "verified":
        return "ok"
    if st == "attested":
        rec = store.record_for(u)
        if require == "attested":
            return "attested"
        if (
            require == "default"
            and rec is not None
            and rec.scope in ("module", "function-unquizzable")
        ):
            return "attested"
    return "violation"


MARKER = "<!-- cdebt-gate -->"


def _label(status: str) -> str:
    return "attested, NON-VERIFIED" if status == "attested" else status


def _span(v: Violation) -> str:
    if not v.ranges:
        return "-"
    return f"{min(a for a, _ in v.ranges)}-{max(b for _, b in v.ranges)}"


def _row(v: Violation, how: str) -> str:
    return f"{v.path}::{v.symbol}  {_span(v)}  {how}"


def to_markdown(r: GateResult, pr: int | None = None) -> str:
    """The sticky PR comment. It marks CODE as understood or not; it never names a person."""
    adv = "" if r.enforced else " (advisory)"
    n = r.ai_lines_unverified
    if r.passed and n == 0:
        head = f"cdebt: all AI-written changed lines are understood{adv}"
    elif r.passed:
        head = (
            f"cdebt: {n} AI-written lines not understood, within the allowed {r.max_ai_lines}{adv}"
        )
    else:
        head = f"cdebt: {n} AI-written lines not understood{adv}"
    out = [MARKER, f"## {head}", ""]
    rows: list[str] = []
    for v in r.violations[:30]:
        why = v.reason or v.status
        how = f"NOT UNDERSTOOD ({why})" if v.status != "attested" else "attested, NON-VERIFIED"
        rows.append(_row(v, how))
    if len(r.violations) > 30:
        rows.append(f"... {len(r.violations) - 30} more")
    for v in r.understood[:20]:
        how = "understood (verified by execution)"
        rows.append(_row(v, how + (", re-checked" if r.rechecked else "")))
    for v in r.attested_accepted[:20]:
        rows.append(_row(v, "accepted on attestation (NON-VERIFIED)"))
    if rows:
        out += ["```text", *rows, "```", ""]
    if n:
        run = f"`cdebt quiz --pr {pr}`" if pr else "`cdebt quiz <file>`"
        out.append(f"Run {run} locally to clear, then comment `/cdebt recheck`.")
        out.append("")
    srcs = ", ".join(f"`{k}`: {v}" for k, v in r.attribution_sources.items()) or "none"
    out.append(
        f"Attribution sources on changed AI lines: {srcs}. Policy: `--require {r.require}`."
        + ("" if r.enforced else " Advisory: this does not block the merge.")
    )
    for note in r.notes:
        out.append(f"\n> {note}")
    out.append(
        "\n<sub>cdebt marks code, not people. A quiz is visibility, not a lie detector.</sub>"
    )
    return "\n".join(out) + "\n"
