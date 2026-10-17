"""CI gate: fail when AI-attributed CHANGED lines sit in units nobody has understood.

Unknown attribution is reported, never silently gated; `strict_unknown` opts in to failing."""

from __future__ import annotations

from pathlib import Path

from cdebt import gitutil
from cdebt.attribution.resolve import default_providers, resolve_file
from cdebt.config import Config
from cdebt.models import AuthorKind, GateResult, Violation
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
    store: Store | None = None,
) -> GateResult:
    head_sha = gitutil.resolve(repo, head)
    head_checked_out = head_sha == gitutil.resolve(repo, "HEAD")
    store = store or Store.at_rev(repo, head_sha, head_sha if head_checked_out else None)
    providers = default_providers(repo, use_trailers=cfg.use_trailers)
    violations: list[Violation] = []
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
        per_unit: dict[str, list[int]] = {}
        file_unknown = 0
        for ln in h.lines:
            if not 1 <= ln <= fa.total:
                continue
            changed_total += 1
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
            if store.status(u) != "valid":
                per_unit.setdefault(u.symbol, []).append(ln)
        if file_unknown:
            unknown_lines += file_unknown
            unknown_files.append(h.path)
        for sym, lns in per_unit.items():
            u = next(x for x in units if x.symbol == sym)
            violations.append(
                Violation(
                    path=h.path,
                    symbol=sym,
                    ai_lines=lns,
                    status=store.status(u),
                    attribution_source=fa.source,
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
        violations=violations,
        unknown_files=sorted(unknown_files),
        unknown_changed_lines=unknown_lines,
        attribution_sources=sources,
        notes=notes,
    )


MARKER = "<!-- cdebt-gate -->"


def to_markdown(r: GateResult) -> str:
    icon = "PASS" if r.passed else "FAIL"
    out = [
        MARKER,
        f"## cdebt gate: {icon}",
        "",
        f"AI-attributed changed lines in units nobody has understood: "
        f"**{r.ai_lines_unverified}** (allowed: {r.max_ai_lines}).",
        "",
    ]
    srcs = ", ".join(f"`{k}`: {v}" for k, v in r.attribution_sources.items()) or "none"
    out.append(f"Attribution sources on changed AI lines: {srcs}.")
    out.append("")
    if r.violations:
        out += ["| file | unit | AI lines | understanding | source |", "|---|---|---|---|---|"]
        for v in r.violations[:30]:
            out.append(
                f"| `{v.path}` | `{v.symbol}` | {len(v.ai_lines)} | {v.status} | "
                f"{v.attribution_source.value} |"
            )
        if len(r.violations) > 30:
            out.append(f"| ... | {len(r.violations) - 30} more | | | |")
        files = sorted({v.path for v in r.violations})
        out += [
            "",
            "To clear: have someone read the code, then run "
            + " / ".join(f"`cdebt quiz {f}`" for f in files[:3])
            + " and commit `.cdebt/understanding.json`. Records expire when the code changes.",
        ]
    for n in r.notes:
        out.append(f"\n> {n}")
    out.append(
        "\n<sub>cdebt measures code, not people. A quiz is visibility, not a lie detector.</sub>"
    )
    return "\n".join(out) + "\n"
